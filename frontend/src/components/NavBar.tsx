const LINKS = ["Analysis", "Positions", "Bars", "Method", "About"];

export default function NavBar() {
  return (
    <nav className="border-b border-ink">
      <div className="mx-auto flex h-11 max-w-[1040px] items-center gap-6 px-6">
        <a href="#" className="label-ink shrink-0 font-bold">
          Candle&#8209;Agent
        </a>
        {/* narrow screens scroll the link strip rather than the whole page */}
        <ul className="no-scrollbar flex min-w-0 flex-1 items-center justify-end gap-5 overflow-x-auto sm:gap-7">
          {LINKS.map((l) => (
            <li key={l} className="shrink-0">
              <a href="#" className="label transition-colors hover:text-ink">
                {l}
              </a>
            </li>
          ))}
        </ul>
      </div>
    </nav>
  );
}
