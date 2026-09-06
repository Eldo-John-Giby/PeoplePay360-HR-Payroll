import { Link, Navigate } from "react-router-dom";
import "../styles.css"
import "../landing.css";
import { useAuth } from "../auth";

// ---------------------------------------------------------------------------
// Tiny inline SVG icons (stroke style, mirrors Odoo's clean line icons).
// ---------------------------------------------------------------------------

function Icon({ children, size = 22 }: { children: React.ReactNode; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {children}
    </svg>
  );
}

const I = {
  clock: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" />
    </>
  ),
  calendar: (
    <>
      <rect x="3" y="5" width="18" height="16" rx="2" />
      <path d="M8 3v4M16 3v4M3 10h18" />
    </>
  ),
  users: (
    <>
      <circle cx="9" cy="8" r="3.4" />
      <path d="M3.5 20c.7-3.2 2.8-5 5.5-5s4.8 1.8 5.5 5" />
      <path d="M16 5.4a3.4 3.4 0 0 1 0 5.2M17.5 15.4c1.8.6 2.9 2.2 3.2 4.6" />
    </>
  ),
  check: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="m8.5 12.5 2.4 2.4 4.6-5" />
    </>
  ),
  shield: (
    <>
      <path d="M12 3 5 6v5c0 4.6 3 8 7 10 4-2 7-5.4 7-10V6l-7-3Z" />
      <path d="m9.5 12 1.8 1.8 3.4-3.8" />
    </>
  ),
  scale: (
    <>
      <path d="M12 3v18M5 8l-2 6a3.5 3.5 0 0 0 7 0L8 8M19 8l-2 6a3.5 3.5 0 0 0 7 0l-2-6" />
      <path d="M5 8h6M13 8h6M12 21h4" />
    </>
  ),
  gauge: (
    <>
      <path d="M4 14a8 8 0 1 1 16 0" />
      <path d="m12 14 4-4" />
      <circle cx="12" cy="14" r="1.6" />
    </>
  ),
  list: (
    <>
      <path d="M9 6h12M9 12h12M9 18h12" />
      <path d="M3.5 6h.01M3.5 12h.01M3.5 18h.01" />
    </>
  ),
  arrow: <path d="M5 12h14m-6-6 6 6-6 6" />,
  logo: (
    <path
      d="M12 2 4 6v6c0 5 3.4 8.6 8 10 4.6-1.4 8-5 8-10V6l-8-4Z"
      strokeWidth={1.6}
    />
  ),
};

// ---------------------------------------------------------------------------
// Decorative "product screenshot" mockups built in CSS so the page needs no
// image assets. They visually echo the real PeoplePay360 console.
// ---------------------------------------------------------------------------

function WindowFrame({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="ld-window">
      <div className="ld-window-bar">
        <span className="ld-dots">
          <i />
          <i />
          <i />
        </span>
        <span className="ld-address">{title}</span>
        <span className="ld-spacer" />
      </div>
      {children}
    </div>
  );
}

function Metric({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone: "ok" | "warn" | "info" | "danger";
}) {
  return (
    <div className="ld-metric">
      <span className={`ld-metric-dot ld-${tone}`} />
      <div>
        <b>{value}</b>
        <span>{label}</span>
      </div>
    </div>
  );
}

function FakeRow({
  name,
  date,
  badge,
  tone,
  hours,
}: {
  name: string;
  date: string;
  badge: string;
  tone: "ok" | "warn" | "info" | "danger";
  hours: string;
}) {
  return (
    <div className="ld-frow">
      <span className="ld-avatar">{name.slice(0, 1)}</span>
      <div className="ld-fname">
        <b>{name}</b>
        <span>{date}</span>
      </div>
      <span className={`ld-chip ld-chip-${tone}`}>{badge}</span>
      <span className="ld-fhours">{hours}</span>
    </div>
  );
}

function AttendanceShot() {
  return (
    <WindowFrame title="PeoplePay360 · Attendance console">
      <div className="ld-shot-head">
        <div className="ld-shot-brand">PeoplePay360</div>
        <div className="ld-shot-nav">
          <i>Attendance</i>
          <i>Time Off</i>
          <i>Balances</i>
          <i>Employees</i>
        </div>
        <span className="ld-shot-user">DN</span>
      </div>
      <div className="ld-shot-body">
        <div className="ld-checkin">
          <div>
            <b>Good morning, Divya</b>
            <span>Wednesday · you&apos;re on the clock</span>
          </div>
          <button type="button" className="ld-checkin-btn">
            ✓ Check in
          </button>
        </div>
        <div className="ld-metrics">
          <Metric label="Present" value="112" tone="ok" />
          <Metric label="Late" value="6" tone="warn" />
          <Metric label="Overtime" value="3" tone="info" />
          <Metric label="Missing check-out" value="2" tone="danger" />
        </div>
        <div className="ld-shot-table">
          <FakeRow
            name="Divya Nair"
            date="Today · 09:02 → 18:00"
            badge="Present"
            tone="ok"
            hours="8h 00m"
          />
          <FakeRow
            name="John D&#39;Souza"
            date="Today · 09:21 → open"
            badge="Late"
            tone="warn"
            hours="-"
          />
          <FakeRow
            name="Aarav Mehta"
            date="Yesterday · 08:55 → 19:10"
            badge="Overtime"
            tone="info"
            hours="10h 15m"
          />
          <FakeRow
            name="Sara Khan"
            date="Yesterday · 09:03 → open"
            badge="Missing check-out"
            tone="danger"
            hours="-"
          />
        </div>
      </div>
    </WindowFrame>
  );
}

function TimeOffShot() {
  return (
    <WindowFrame title="PeoplePay360 · Time off requests">
      <div className="ld-shot-body ld-two">
        <div className="ld-col">
          <h4>Pending approval</h4>
          <div className="ld-req">
            <span className="ld-avatar">J</span>
            <div className="ld-fname">
              <b>John D&#39;Souza</b>
              <span>Paid Time Off · Aug 12 - 14</span>
            </div>
            <span className="ld-chip ld-chip-warn">To approve</span>
          </div>
          <div className="ld-req">
            <span className="ld-avatar">S</span>
            <div className="ld-fname">
              <b>Sara Khan</b>
              <span>Personal Leave · Aug 20</span>
            </div>
            <span className="ld-chip ld-chip-warn">To approve</span>
          </div>
          <div className="ld-req-btns">
            <button type="button" className="ld-ok-btn">
              Approve
            </button>
            <button type="button" className="ld-ghost-btn">
              Refuse
            </button>
          </div>
        </div>
        <div className="ld-col">
          <h4>Live balances</h4>
          <div className="ld-balance">
            <div className="ld-bal-top">
              <span>Paid Time Off</span>
              <b>18 of 24 days</b>
            </div>
            <div className="ld-bar">
              <i style={{ width: "75%" }} />
            </div>
          </div>
          <div className="ld-balance">
            <div className="ld-bal-top">
              <span>Sick Leave</span>
              <b>10 of 12 days</b>
            </div>
            <div className="ld-bar">
              <i style={{ width: "83%" }} />
            </div>
          </div>
          <div className="ld-balance">
            <div className="ld-bal-top">
              <span>Casual Leave</span>
              <b>9 of 10 days</b>
            </div>
            <div className="ld-bar">
              <i style={{ width: "90%" }} />
            </div>
          </div>
          <p className="ld-note">Balances are always computed live - nothing pre-deducted.</p>
        </div>
      </div>
    </WindowFrame>
  );
}

// ---------------------------------------------------------------------------
// Feature tile data
// ---------------------------------------------------------------------------

const FEATURES = [
  {
    icon: I.clock,
    title: "Smart attendance",
    text: "Check in / check out with automatic late, overtime and missing check-out detection.",
  },
  {
    icon: I.calendar,
    title: "Time off requests",
    text: "Employees ask for leave in seconds; HR approves or refuses with one click.",
  },
  {
    icon: I.scale,
    title: "Live balances",
    text: "Allocated minus taken - computed from approved data on every read. Never drifts.",
  },
  {
    icon: I.check,
    title: "Approvals workflow",
    text: "A strict state machine: requests and allocations only move through valid states.",
  },
  {
    icon: I.shield,
    title: "Role-based access",
    text: "Employees see their own records; HR manages everyone. Every action is gated.",
  },
  {
    icon: I.users,
    title: "Employee directory",
    text: "The whole team in one place - profile, department, schedule and linked account.",
  },
];

const CHECKS_A = [
  "One-tap check-in / check-out from any device",
  "Late, overtime and missing check-out flags derived from each employee's working schedule",
  "Manual corrections by HR with full audit trail",
  "Per-employee summary for dashboards and smart buttons",
];

const CHECKS_B = [
  "Request time off in the type's own unit - days or hours",
  "Approve or refuse with balance checks built in - no negative leave",
  "Overlapping requests are caught before they're approved",
  "Grant and approve allocations; expiry windows never count twice",
];

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export function LandingPage() {
  const { user } = useAuth();
  if (user) return <Navigate to="/attendance" replace />;

  return (
    <div className="ld" id="top">
      {/* Top navigation -------------------------------------------------- */}
      <header className="ld-nav">
        <Link to="/" className="ld-brand">
          PeoplePay360
        </Link>
        <nav className="ld-links">
          <a href="#features">Features</a>
          <a href="#attendance">Attendance</a>
          <a href="#timeoff">Time off</a>
        </nav>
        <div className="ld-nav-cta">
          <Link className="ld-login" to="/login">
            Log in
          </Link>
        </div>
      </header>

      {/* Hero ------------------------------------------------------------ */}
      <section className="ld-hero">
        <div className="ld-hero-inner">
          <span className="ld-eyebrow">
            <i />
            Attendance, time off &amp; employee self-service
          </span>
          <h1>
            Your people, their hours -
            <br />
            <em>all in one place.</em>
          </h1>
          <p className="ld-lead">
            PeoplePay360 is the HR console for attendance tracking and time-off
            management. Check in, ask for leave, approve requests and watch live
            balances - without a single spreadsheet.
          </p>
          <div className="ld-hero-cta">
            <Link className="ld-btn ld-btn-dark" to="/login">
              Start now - it&apos;s free
              <Icon size={16}>{I.arrow}</Icon>
            </Link>
            <a className="ld-btn ld-btn-ghost" href="#features">
              Explore the features
            </a>
          </div>
          <p className="ld-hero-note">
            Free demo · seeded with realistic data · one-click sign-in for every role
          </p>
        </div>

        <div className="ld-hero-shot">
          <AttendanceShot />
        </div>
      </section>

      {/* Feature sections ------------------------------------------------ */}
      <section id="features" className="ld-feature ld-feature-alt">
        <div className="ld-feature-inner">
          <div className="ld-feature-text">
            <span className="ld-kicker">Attendance</span>
            <h2>Every hour, accounted for.</h2>
            <p>
              Check in when you arrive, check out when you leave - PeoplePay360
              derives the rest. Late? Overtime? Missing check-out? Each status is
              computed from the employee&apos;s own working schedule, never guessed.
            </p>
            <ul className="ld-checks">
              {CHECKS_A.map((c) => (
                <li key={c}>
                  <span className="ld-tick">
                    <Icon size={14}>{I.check}</Icon>
                  </span>
                  {c}
                </li>
              ))}
            </ul>
          </div>
          <div className="ld-feature-shot">
            <AttendanceShot />
          </div>
        </div>
      </section>

      <section id="timeoff" className="ld-feature">
        <div className="ld-feature-inner ld-feature-flip">
          <div className="ld-feature-shot">
            <TimeOffShot />
          </div>
          <div className="ld-feature-text">
            <span className="ld-kicker">Time off</span>
            <h2>Leave without the spreadsheet chase.</h2>
            <p>
              Employees request leave in the type&apos;s own unit. HR approves or
              refuses against a balance that&apos;s always live - so approving a
              request can never push a team member into negative leave.
            </p>
            <ul className="ld-checks">
              {CHECKS_B.map((c) => (
                <li key={c}>
                  <span className="ld-tick">
                    <Icon size={14}>{I.check}</Icon>
                  </span>
                  {c}
                </li>
              ))}
            </ul>
          </div>
        </div>
      </section>

      {/* Feature tiles --------------------------------------------------- */}
      <section className="ld-tiles">
        <div className="ld-tiles-inner">
          <span className="ld-kicker">All the features, done right</span>
          <h2>One need, one app. Expand as you grow.</h2>
          <div className="ld-tile-grid">
            {FEATURES.map((f) => (
              <div className="ld-tile" key={f.title}>
                <span className="ld-tile-icon">
                  <Icon size={22}>{f.icon}</Icon>
                </span>
                <h3>{f.title}</h3>
                <p>{f.text}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Quote ----------------------------------------------------------- */}
      <section className="ld-quote">
        <div className="ld-quote-inner">
          <div className="ld-quote-card">
            <span className="ld-quote-mark">“</span>
            <p>
              Everything a people team needs for the day-to-day - attendance
              flags, approvals, balances - actually works end to end. That&apos;s
              rare in an HR tool.
            </p>
            <div className="ld-quote-who">
              <span className="ld-avatar ld-avatar-lg">T</span>
              <div>
                <b>Demo reviewer</b>
                <span>People team · OXP Hackathon</span>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Final CTA ------------------------------------------------------- */}
      <section className="ld-cta">
        <div className="ld-cta-inner">
          <h2>Ready to run HR without the guesswork?</h2>
          <p>Sign in with a demo account for any role and click through the full flow.</p>
          <Link className="ld-btn ld-btn-dark ld-btn-lg" to="/login">
            Start now - it&apos;s free
            <Icon size={17}>{I.arrow}</Icon>
          </Link>
          <p className="ld-cta-note">
            No credit card · no setup · seeded demo data included
          </p>
        </div>
      </section>

      {/* Footer ---------------------------------------------------------- */}
      <footer className="ld-footer">
        <div className="ld-footer-inner">
          <Link to="/" className="ld-brand">
            PeoplePay360
          </Link>
          <div className="ld-footer-cols">
            <div className="ld-fcol">
              <h5>Product</h5>
              <a href="#features">Features</a>
              <a href="#attendance">Attendance</a>
              <a href="#timeoff">Time off</a>
              <Link to="/login">Sign in</Link>
            </div>
            <div className="ld-fcol">
              <h5>Roles</h5>
              <span>Employee self-service</span>
              <span>HR manager</span>
              <span>Payroll (coming soon)</span>
            </div>
            <div className="ld-fcol">
              <h5>Stack</h5>
              <span>FastAPI + PostgreSQL</span>
              <span>React + TypeScript</span>
              <span>SQLAlchemy 2.0</span>
            </div>
          </div>
        </div>
        <div className="ld-footer-bottom">
          <span>© 2026 PeoplePay360 · Built for the OXP 24-hour hackathon</span>
          <a href="#top">Back to top ↑</a>
        </div>
      </footer>
    </div>
  );
}
