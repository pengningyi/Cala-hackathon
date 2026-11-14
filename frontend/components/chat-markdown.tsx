"use client";

import { memo } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkBreaks from "remark-breaks";
import remarkGfm from "remark-gfm";
import { CITE_PREFIX, linkCitations } from "../lib/chat-citations";
import type { Source } from "../lib/types";

interface ChatMarkdownProps {
  content: string;
  citations: Source[];
  citationIds: string[];
}

/** Renders an assistant answer as markdown. Raw HTML is never rendered (react-markdown default). */
function ChatMarkdown({ content, citations, citationIds }: ChatMarkdownProps) {
  const components: Components = {
    a({ href, children }) {
      if (href?.startsWith(CITE_PREFIX)) {
        const n = Number(href.slice(CITE_PREFIX.length));
        const source = citations[n - 1];
        if (!source) return <sup className="rc-cite">{n}</sup>;
        return <sup className="rc-cite"><a href={source.url} target="_blank" rel="noopener noreferrer"
          title={source.label} aria-label={`Source ${n}: ${source.label}`}>{n}</a></sup>;
      }
      return <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>;
    },
    table({ children }) {
      return <div className="rc-md-table"><table>{children}</table></div>;
    },
  };
  return <div className="rc-chat-markdown">
    <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]} components={components}
      disallowedElements={["img"]} unwrapDisallowed>
      {linkCitations(content, citationIds)}
    </ReactMarkdown>
  </div>;
}

export default memo(ChatMarkdown);
