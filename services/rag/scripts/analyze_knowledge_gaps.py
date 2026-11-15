import os
import re
import glob
from collections import defaultdict

# 1. Read all markdown files from data/gosapteka56/2026-10-27/rag-prepared/
folder = "/source/data/gosapteka56/2026-10-27/rag-prepared"
md_files = glob.glob(os.path.join(folder, "*.md"))
print(f"Total markdown files found: {len(md_files)}")

# Extract all mentions of:
# - законы, федеральные законы (ФЗ)
# - постановления правительства РФ и области
# - распоряжения и приказы Минздрава РФ и ОО
# - упомянутые системы, протоколы, сервисы, ошибки, регламенты
# - слова-индикаторы пробелов ("не указано", "нет данных", "уточняется", "TODO", "в разработке", "приложение №")

references = defaultdict(set)
indicators = []
all_text = ""

for path in md_files:
    fname = os.path.basename(path)
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()
    
    # Check for gaps indicators
    for line_no, line in enumerate(content.splitlines(), 1):
        if any(term in line.lower() for term in ["todo", "не указано", "нет данных", "уточнить", "уточняется", "в разработке", "отсутствует информация", "заглушка"]):
            indicators.append((fname, line_no, line.strip()))
            
    # Search for regexes:
    # 1. Приказы и распоряжения
    prikazy = re.findall(r"(?:приказ|распоряжени[ея]|постановлени[ея]|закон[а-я]*|№)\s+(?:МЗ\s+РФ|Минздрава\s+РФ|МЗ\s+ОО|Минздрава\s+Оренбургской\s+области|Правительства\s+РФ|Правительства\s+Оренбургской\s+области)?\s*(?:от\s+[\d\.]+\s+)?(?:№\s*[\d\w\-\/н]+)", content, re.IGNORECASE)
    for p in prikazy:
        norm = re.sub(r"\s+", " ", p).strip()
        references["regulations"].add((fname, norm))
        
    # 2. МДЛП схемы / ошибки / коды
    mdlp = re.findall(r"(?:схема|операция|тип выбытия|ошибка|код)\s+(?:МДЛП\s+)?(?:№\s*)?(\d{3,5})", content, re.IGNORECASE)
    for m in mdlp:
        references["mdlp_schemes"].add((fname, m))
        
    # 3. Упоминания сервисов и систем
    systems = re.findall(r"(ЕГИСЗ|ГИСЗ\s*ОО|ИС\s*ЛО|ЦПС\s*ЛЛО|МДЛП|Честный\s*Знак|РВ|ФРМР|ФРМО|НСИ|КМХД|СЭМД|СМЭВ|ЭЦП|УКЭП)", content, re.IGNORECASE)
    for s in systems:
        references["systems"].add(s.upper())

print(f"\n--- Indicators of potential gaps ({len(indicators)} found) ---")
for fn, lno, text in indicators[:20]:
    print(f"[{fn}:{lno}] {text}")

print(f"\n--- Mentioned Systems ---")
print(sorted(references["systems"]))

# Check list of laws / decrees / orders mentioned across files
reg_list = defaultdict(list)
for fn, reg in references["regulations"]:
    reg_list[reg].append(fn)

print(f"\n--- Top Mentioned Regulations across documents ({len(reg_list)} unique) ---")
sorted_regs = sorted(reg_list.items(), key=lambda x: len(x[1]), reverse=True)
for reg, files in sorted_regs[:30]:
    print(f"{reg} -> mentioned in {len(files)} files (e.g. {files[0]})")
