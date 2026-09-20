import { Children, cloneElement, isValidElement, ReactNode } from "react";
import ReactMarkdown from "react-markdown";

/**
 * The answer as markdown, with every `[n]` marker turned into a button that opens that source.
 *
 * The markers are the product: a claim and its evidence should never be more than one click apart,
 * so they are rendered as controls rather than plain text.
 */

interface Props {
  markdown: string;
  citationIds: number[];
  activeId?: number;
  onSelect: (citationId: number) => void;
}

const MARKER = /(\[\d+\])/g;

function markerize(node: ReactNode, valid: Set<number>, activeId: number | undefined,
                   onSelect: (id: number) => void, keyPrefix = "m"): ReactNode {
  if (typeof node === "string") {
    if (!MARKER.test(node)) return node;
    MARKER.lastIndex = 0;
    return node.split(MARKER).map((part, index) => {
      const match = /^\[(\d+)\]$/.exec(part);
      const id = match ? Number(match[1]) : undefined;
      if (id === undefined || !valid.has(id)) return part;
      return (
        <button
          key={`${keyPrefix}-${index}`}
          type="button"
          className={`cite-marker${activeId === id ? " is-active" : ""}`}
          onClick={() => onSelect(id)}
          aria-label={`Show source ${id}`}
        >
          {id}
        </button>
      );
    });
  }
  if (Array.isArray(node)) {
    return Children.map(node, (child, index) => markerize(child, valid, activeId, onSelect, `${keyPrefix}-${index}`));
  }
  if (isValidElement(node)) {
    const children = (node.props as { children?: ReactNode }).children;
    if (children === undefined) return node;
    return cloneElement(node, undefined, markerize(children, valid, activeId, onSelect, keyPrefix));
  }
  return node;
}

export default function AnswerProse({ markdown, citationIds, activeId, onSelect }: Props) {
  const valid = new Set(citationIds);
  const wrap = (children: ReactNode) => markerize(children, valid, activeId, onSelect);
  return (
    <div className="answer-prose">
      <ReactMarkdown
        components={{
          p: ({ children }) => <p>{wrap(children)}</p>,
          li: ({ children }) => <li>{wrap(children)}</li>,
          td: ({ children }) => <td>{wrap(children)}</td>,
          th: ({ children }) => <th>{wrap(children)}</th>,
          h1: ({ children }) => <h3>{wrap(children)}</h3>,
          h2: ({ children }) => <h3>{wrap(children)}</h3>,
          h3: ({ children }) => <h4>{wrap(children)}</h4>,
          blockquote: ({ children }) => <blockquote>{wrap(children)}</blockquote>,
        }}
      >
        {markdown}
      </ReactMarkdown>
    </div>
  );
}
