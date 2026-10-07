import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

export function Markdown({ children, internalLinks = {} }: { children: string; internalLinks?: Record<string, string> }) {
  const content = children.replace(/\[\[([^\]|]+)(?:\|([^\]]+))?\]\]/g, (_match, target: string, label?: string) => {
    const href = internalLinks[target.trim().toLowerCase()];
    return href ? `[${label || target}](${href})` : label || target;
  });
  return (
    <div className="markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ children: label, href }) => (
            <a href={href} rel={href?.startsWith("/zopedia?") ? undefined : "noreferrer noopener"} target={href?.startsWith("/zopedia?") ? undefined : "_blank"}>{label}</a>
          ),
          table: ({ children: body }) => (
            <div className="markdown-table-wrap"><table>{body}</table></div>
          ),
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
