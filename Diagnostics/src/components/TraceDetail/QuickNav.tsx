import { memo } from 'react';
import { GitBranch, Search, Database, Code, FileCheck, MessageSquare, ShieldCheck } from 'lucide-react';
import { type TraceEvent } from '../../types';

interface QuickNavProps {
  events: TraceEvent[];
  onJumpToStage: (stage: string) => void;
}

interface NavItem {
  id: string;
  stage: string;
  title: string;
  icon: typeof GitBranch;
  matcher: (event: TraceEvent) => boolean;
}

const NAV_ITEMS: NavItem[] = [
  {
    id: 'branch',
    stage: 'specialist_dispatch',
    title: 'Ветка',
    icon: GitBranch,
    matcher: e => e.stage === 'specialist_dispatch' || e.stage === 'route',
  },
  {
    id: 'retrieval',
    stage: 'retrieval',
    title: 'Поиск и отбор',
    icon: Search,
    matcher: e => e.stage === 'retrieval' || e.stage === 'crag_retrieve',
  },
  {
    id: 'db',
    stage: 'db_generate_sql',
    title: 'БД / SQL',
    icon: Database,
    matcher: e => e.stage.startsWith('db_'),
  },
  {
    id: 'code',
    stage: 'coding_generation',
    title: 'Код',
    icon: Code,
    matcher: e => e.stage.startsWith('coding_'),
  },
  {
    id: 'context',
    stage: 'context',
    title: 'Контекст',
    icon: FileCheck,
    matcher: e => e.stage === 'context',
  },
  {
    id: 'model',
    stage: 'generation',
    title: 'Модель',
    icon: MessageSquare,
    matcher: e => e.stage === 'generation',
  },
  {
    id: 'answer',
    stage: 'answer',
    title: 'Ответ',
    icon: ShieldCheck,
    matcher: e => e.stage === 'answer',
  },
];

export const QuickNav = memo(function QuickNav({ events, onJumpToStage }: QuickNavProps) {
  return (
    <nav className="quick-pipeline-nav" aria-label="Быстрый переход к этапам пайплайна">
      <div className="nav-items-wrapper">
        {NAV_ITEMS.map(({ id, stage, title, icon: Icon, matcher }) => {
          const matchingCount = events.filter(matcher).length;
          const isDisabled = matchingCount === 0;

          return (
            <button
              key={id}
              type="button"
              className={`pipeline-nav-btn ${isDisabled ? 'disabled' : ''}`}
              onClick={() => onJumpToStage(stage)}
              disabled={isDisabled}
              title={isDisabled ? `Этап "${title}" отсутствует в трассе` : `Перейти к этапу "${title}"`}
            >
              <Icon size={14} className="nav-btn-icon" aria-hidden="true" />
              <span className="nav-btn-title">{title}</span>
              <span className="nav-count-badge">{matchingCount}</span>
            </button>
          );
        })}
      </div>
    </nav>
  );
});
