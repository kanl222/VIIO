"""Reproduce context formatting with each agent's own installed package.

No generation or external requests; run separately in Assistent/Consultant images.
Raw contexts stay in the supplied diagnostic output directory.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import re

async def main(folder,agent):
    folder=Path(folder)
    rows=[json.loads(line) for line in (folder/'results.jsonl').read_text(encoding='utf-8').splitlines()]
    result=[]
    def markers(text,patterns):return {name:bool(re.search(pattern,text,re.I|re.S)) for name,pattern in patterns.items()}
    if agent=='main':
        from langchain_core.messages import AIMessage,HumanMessage,ToolMessage
        from assistent.services.graph_builder import messages_with_retrieved_context
        from assistent.services.dialogue_summary import compact_dialogue
        budget=int(os.getenv('RAG_CONTEXT_MAX_CHARS','12000'))
        for row in rows:
            path=folder/'contexts'/(row['case']+'-'+row['variant'])
            source=Path(str(path)+'-main-tool.txt').read_text(encoding='utf-8')
            snapshot=json.loads(Path(str(path)+'-public-snapshots.json').read_text(encoding='utf-8'))
            counts={}
            for mode,identifier in [('model_tool_call','model-diagnostic'),('application_fallback','knowledge-diagnostic')]:
                messages=[HumanMessage(content=row['query']),AIMessage(content='',tool_calls=[{'name':'search_knowledge_base','id':identifier,'args':{'query':row['query']}}]),
                    ToolMessage(content=source,tool_call_id=identifier,name='search_knowledge_base')]
                bounded,_=await compact_dialogue(messages,{'history_scope':'diagnostic'},{'history_scope':'diagnostic'},None,24,18000)
                prepared=messages_with_retrieved_context(bounded,budget)
                if mode=='model_tool_call':text=prepared[-1].content
                else:text=prepared[-1].content.split('\n\nВопрос пользователя:\n')[0]
                Path(str(path)+'-'+mode+'.txt').write_text(text,encoding='utf-8')
                counts[mode]={'characters':len(text),'contains':markers(text,snapshot['patterns'])}
            result.append({'case':row['case'],'variant':row['variant'],'configured_rag_context_max_chars':budget,**counts})
    else:
        from viio_consultant.agent import excerpt
        for row in rows:
            path=folder/'contexts'/(row['case']+'-'+row['variant'])
            snapshot=json.loads(Path(str(path)+'-public-snapshots.json').read_text(encoding='utf-8'))
            windows=[]
            for rank,item in enumerate(snapshot['items'],1):
                text,line=excerpt(item['content'],snapshot['query'],3000)
                windows.append({'rank':rank,'selected_on_first_call':rank<=2,'publication_id':item['id'],'title':item['title'],
                    'full_characters':len(item['content']),'window_characters':len(text),'window_start_line':line,
                    'full_contains':markers(item['content'],snapshot['patterns']),'window_contains':markers(text,snapshot['patterns']),
                    'sample':text[:500]})
                Path(str(path)+'-consultant-window-'+str(rank)+'.txt').write_text(text,encoding='utf-8')
            result.append({'case':row['case'],'variant':row['variant'],'query':row['query'],'windows':windows})
    target=folder/(agent+'-contexts.json')
    target.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'agent':agent,'queries':len(result),'output':target.name},ensure_ascii=False))

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder',default='/output')
    parser.add_argument('--agent',choices=['main','consultant'],required=True)
    args=parser.parse_args()
    asyncio.run(main(args.folder,args.agent))
