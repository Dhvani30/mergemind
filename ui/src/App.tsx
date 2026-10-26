function MergeMindMark() {
  return (
    <svg
      aria-hidden="true"
      className="brand-mark"
      fill="none"
      viewBox="0 0 36 36"
    >
      <path
        d="M8 10.5h7.2a4 4 0 0 1 4 4v7a4 4 0 0 0 4 4H28M8 25.5h7.2a4 4 0 0 0 4-4v-7a4 4 0 0 1 4-4H28"
        stroke="currentColor"
        strokeLinecap="round"
        strokeWidth="2.2"
      />
      <circle cx="8" cy="10.5" r="2.5" fill="currentColor" />
      <circle cx="8" cy="25.5" r="2.5" fill="currentColor" />
      <circle cx="28" cy="10.5" r="2.5" fill="currentColor" />
      <circle cx="28" cy="25.5" r="2.5" fill="currentColor" />
      <circle cx="19.2" cy="18" r="2.6" fill="#101311" stroke="currentColor" strokeWidth="2" />
    </svg>
  );
}

function WorkspaceIcon() {
  return (
    <svg aria-hidden="true" fill="none" viewBox="0 0 20 20">
      <rect
        height="13"
        rx="2.5"
        stroke="currentColor"
        strokeWidth="1.5"
        width="15"
        x="2.5"
        y="3.5"
      />
      <path
        d="M7 3.8v12.4M10 7h4.5M10 10h4.5"
        stroke="currentColor"
        strokeLinecap="round"
        strokeWidth="1.5"
      />
    </svg>
  );
}

function App() {
  return (
    <>
      <a className="skip-link" href="#main">
        Skip to content
      </a>

      <div className="app-shell">
        <header className="topbar">
          <a aria-label="MergeMind home" className="brand" href="#main">
            <MergeMindMark />
            <span>merge<span className="brand-accent">mind</span></span>
          </a>

          <nav aria-label="Main navigation" className="primary-nav">
            <a aria-current="page" className="nav-item" href="#main">
              <WorkspaceIcon />
              <span>Workspace</span>
            </a>
          </nav>

          <div aria-label="Application status" className="topbar-status">
            <span aria-hidden="true" className="status-dot" />
            <span>Local workspace</span>
          </div>
        </header>

        <main className="main-content" id="main">
          <div className="page-heading">
            <div>
              <p className="eyebrow">MERGEMIND / WORKSPACE</p>
              <h1>Your workspace</h1>
              <p className="page-description">
                A focused place to understand and resolve code conflicts.
              </p>
            </div>
            <span className="foundation-badge">Foundation</span>
          </div>

          <section aria-labelledby="empty-title" className="empty-state">
            <div aria-hidden="true" className="empty-icon">
              <WorkspaceIcon />
            </div>
            <p className="eyebrow">READY WHEN YOU ARE</p>
            <h2 id="empty-title">Your workspace is ready to be connected.</h2>
            <p className="empty-description">
              Repository selection and merge workflows will be added in a
              future feature. This foundation does not connect to the backend
              yet.
            </p>
            <div className="capability-note">
              <span aria-hidden="true" className="capability-indicator" />
              <span>Frontend foundation · No repository connected</span>
            </div>
          </section>

          <footer className="page-footer">
            <span>MergeMind</span>
            <span>Local-first merge workspace</span>
          </footer>
        </main>
      </div>
    </>
  );
}

export default App;
