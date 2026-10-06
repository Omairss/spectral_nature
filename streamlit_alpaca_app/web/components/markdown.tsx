import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

export function Markdown({ children }: { children: string }) {
  return (
    <div className="markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ children: label, href }) => (
            <a href={href} rel="noreferrer noopener" target="_blank">{label}</a>
          ),
          table: ({ children: body }) => (
            <div className="markdown-table-wrap"><table>{body}</table></div>
          ),
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
