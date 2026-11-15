import { memo } from 'react';
import { GitBranch, Search, Database, Code, FileCheck, MessageSquare, ShieldCheck, Star } from 'lucide-react';
import { type TraceEvent } from '../../types';

interface QuickNavProps {
  events: TraceEvent[];
  onJumpToMatcher: (matcher: (event: TraceEvent) => boolean) => void;
  onJumpToReview?: () => void;
  hasReview?: boolean;
}

interface NavItem {
  id: string;
  title: string;
  icon: typeof GitBranch;
  matcher: (event: TraceEvent) => boolean;
  countMatcher?: (event: TraceEvent) => boolean;
}

const isDbEvent = (e: TraceEvent) =>
  e.stage.startsWith('db_') ||
  ((e.stage === 'tool_start' || e.stage === 'tool_end') && e.name === 'postgresql_readonly');

const isCodeEvent = (e: TraceEvent) =>
  e.stage.startsWith('coding_') ||
  ((e.stage === 'tool_start' || e.stage === 'tool_end') &&
    (e.name === 'coding_agent' || e.name === 'github_repository'));

const isRetrievalEvent = (e: TraceEvent) =>
  e.stage === 'retrieval' ||
  e.stage === 'crag_retrieve' ||
  ((e.stage === 'tool_start' || e.stage === 'tool_end') &&
    (e.name === 'search_knowledge_base' || e.name === 'list_documents' || e.name === 'search_document_content'));

// Avoid double-counting tool_start + tool_end for single tool invocation
const countToolOnce = (fn: (e: TraceEvent) => boolean) => (e: TraceEvent) => {
  if (!fn(e)) return false;
  if (e.stage === 'tool_end') return false;
  return true;
};

const NAV_ITEMS: NavItem[] = [
  {
    id: 'branch',
    title: 'Ветка',
    icon: GitBranch,
    matcher: e => e.stage === 'specialist_dispatch' || e.stage === 'route',
    countMatcher: e => e.stage === 'specialist_dispatch' || (e.stage === 'route' && Boolean(e.supervisor)),
  },
  {
    id: 'retrieval',
    title: 'Поиск и отбор',
    icon: Search,
    matcher: isRetrievalEvent,
    countMatcher: countToolOnce(isRetrievalEvent),
  },
  {
    id: 'db',
    title: 'БД / SQL',
    icon: Database,
    matcher: isDbEvent,
    countMatcher: countToolOnce(isDbEvent),
  },
  {
    id: 'code',
    title: 'Код',
    icon: Code,
    matcher: isCodeEvent,
    countMatcher: countToolOnce(isCodeEvent),
  },
  {
    id: 'context',
    title: 'Контекст',
    icon: FileCheck,
    matcher: e => e.stage === 'context',
  },
  {
    id: 'model',
    title: 'Модель',
    icon: MessageSquare,
    matcher: e => e.stage === 'generation',
  },
  {
    id: 'answer',
    title: 'Ответ',
    icon: ShieldCheck,
    matcher: e => e.stage === 'answer',
  },
];

export const QuickNav = memo(function QuickNav({
  events,
  onJumpToMatcher,
  onJumpToReview,
  hasReview,
}: QuickNavProps) {
  return (
    <nav className="quick-pipeline-nav" aria-label="Быстрый переход к этапам пайплайна">
      <div className="nav-items-wrapper">
        {NAV_ITEMS.map(({ id, title, icon: Icon, matcher, countMatcher }) => {
          const matchingCount = events.filter(countMatcher || matcher).length;
          const isDisabled = matchingCount === 0;

          return (
            <button
              key={id}
              type="button"
              className={`pipeline-nav-btn ${isDisabled ? 'disabled' : ''}`}
              onClick={() => onJumpToMatcher(matcher)}
              disabled={isDisabled}
              title={isDisabled ? `Этап "${title}" отсутствует в трассе` : `Перейти к этапу "${title}"`}
            >
              <Icon size={14} className="nav-btn-icon" aria-hidden="true" />
              <span className="nav-btn-title">{title}</span>
              <span className="nav-count-badge">{matchingCount}</span>
            </button>
          );
        })}

        {onJumpToReview ? (
          <button
            type="button"
            className={`pipeline-nav-btn review-nav-btn ${hasReview ? 'has-review' : ''}`}
            onClick={onJumpToReview}
            title="Перейти к блоку экспертного отзыва и оценки качества"
          >
            <Star size={14} className="nav-btn-icon" aria-hidden="true" style={{ color: 'var(--amber, #f59e0b)' }} />
            <span className="nav-btn-title">Отзыв</span>
            <span className={`nav-count-badge ${hasReview ? 'reviewed-badge' : ''}`}>
              {hasReview ? '✓' : '0'}
            </span>
          </button>
        ) : null}
      </div>
    </nav>
  );
});
