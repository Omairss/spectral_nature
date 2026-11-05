import Link from "next/link";
import { AttentionStory } from "@/components/attention-story";
import { AttentionTickerCard } from "@/components/attention-ticker-card";
import type { HomeBeat, HomeView } from "@/lib/types";

export function AttentionEvent({ home, beat, images = false }: { home: HomeView; beat: HomeBeat; images?: boolean }) {
  const stamp = new Intl.DateTimeFormat("en-US", { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" }).format(new Date(home.as_of)) + " UTC";
  const brand = <header className="attention-share-brand"><strong>Spectral Nature</strong><span>Attention · {stamp}</span></header>;
  if (images) {
    const groups = [beat.tickers.slice(0, 2), beat.tickers.slice(2, 4)].filter(group => group.length);
    return <div className="attention-images" data-source-version={home.source_version} data-source-run={home.run_id} data-event-id={beat.id}>
      {groups.map((tickers, index) => <section className="attention-image" data-attention-image key={index} data-image-description={`Charts and company data for ${tickers.map(ticker => ticker.symbol).join(" and ")}. Snapshot ${stamp}.`}>
        {brand}<h2>{beat.title}</h2>
        <div className={`attention-image-tickers${tickers.length === 1 ? " single" : ""}`}>{tickers.map(ticker => <AttentionTickerCard key={ticker.symbol} ticker={ticker} />)}</div>
        <footer>spectral-nature.com · {stamp}</footer>
      </section>)}
      <section className="attention-image attention-image-story" data-attention-image data-image-description={`${beat.title} ${beat.summary}`}>
        {brand}<div className="attention-story"><AttentionStory beat={beat} /></div>
        <footer>spectral-nature.com · {beat.symbols.join(" · ")}</footer>
      </section>
    </div>;
  }
  return <div className="page-shell attention-event-page">
    <header className="artifact-header"><div><p className="breadcrumb"><Link href="/">Home</Link> <span>/</span> Attention</p><time dateTime={home.as_of}>{stamp}</time></div></header>
    <article className="attention-story"><AttentionStory beat={beat} /></article>
    <div className="attention-ticker-grid">{beat.tickers.map(ticker => <AttentionTickerCard key={ticker.symbol} ticker={ticker} />)}</div>
    <Link className="attention-return" href={`/?date=${home.view_date}`}>Daily Brief · {home.view_date}</Link>
  </div>;
}
