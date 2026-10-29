import { FormEvent, useEffect, useRef, useState } from "react";

const API = import.meta.env.VITE_API_BASE_URL || "/api/v1";

type User = { id: string; email: string };
type Analysis = {
  id: string;
  status: string;
  error_message: string | null;
  result: Record<string, unknown> | null;
  resume_filename: string;
};
type KnowledgeBase = { id: string; name: string };
type KnowledgeDocument = { id: string; original_filename: string; status: string };
type Interview = {
  id: string;
  role: string;
  status: string;
  current_question: string | null;
  turns: Array<{ question: string; answer: string; evaluation: Record<string, unknown> }>;
  report: Record<string, unknown> | null;
};
type ScheduleEvent = {
  id: string;
  title: string;
  company: string | null;
  starts_at: string;
  meeting_url: string | null;
};
type Notification = {
  id: string;
  title: string;
  body: string;
  read_at: string | null;
  created_at: string;
};

const sections = ["Overview", "Resume analysis", "Knowledge base", "Mock interview", "Schedule", "Speech", "Settings"];

async function api<T>(
  path: string,
  token: string,
  init: RequestInit = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${token}`);
  if (
    init.body &&
    !(init.body instanceof FormData) &&
    !(init.body instanceof URLSearchParams)
  ) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(`${API}${path}`, { ...init, headers });
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      message = body.detail || message;
    } catch {
      message = response.statusText || message;
    }
    throw new Error(message);
  }
  if (response.status === 204) return undefined as T;
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) return response.json() as Promise<T>;
  return response.blob() as Promise<T>;
}

function App() {
  const [token, setToken] = useState(() => localStorage.getItem("access_token") || "");
  const [user, setUser] = useState<User | null>(null);
  const [section, setSection] = useState("Overview");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  async function refreshUser() {
    if (!token) return;
    try {
      setUser(await api<User>("/auth/me", token));
    } catch {
      localStorage.removeItem("access_token");
      setToken("");
      setUser(null);
    }
  }

  useEffect(() => {
    void refreshUser();
  }, [token]);

  async function run<T>(operation: () => Promise<T>): Promise<T | undefined> {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      return await operation();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unexpected error");
      return undefined;
    } finally {
      setBusy(false);
    }
  }

  function onLogin(nextToken: string) {
    localStorage.setItem("access_token", nextToken);
    setToken(nextToken);
  }

  function signOut() {
    localStorage.removeItem("access_token");
    setToken("");
    setUser(null);
  }

  if (!token || !user) {
    return <AuthScreen onLogin={onLogin} run={run} busy={busy} error={error} />;
  }

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand"><span className="brand-icon">✦</span><div><b>Interview Guide</b><small>AI career workspace</small></div></div>
        <nav>{sections.map((item) => <button className={section === item ? "nav-item active" : "nav-item"} key={item} onClick={() => setSection(item)}><span>{iconFor(item)}</span>{item}</button>)}</nav>
        <div className="sidebar-bottom"><div className="user-card"><div className="avatar">{user.email[0].toUpperCase()}</div><div className="user-copy"><b>{user.email}</b><small>Candidate workspace</small></div><button className="icon-button" title="Sign out" onClick={signOut}>↗</button></div></div>
      </aside>
      <main className="main">
        <header className="topbar"><div className="breadcrumbs">Workspace <span>/</span> {section}</div><div className="topbar-right"><span className="status-dot" /> AI services ready <div className="avatar small">{user.email[0].toUpperCase()}</div></div></header>
        <div className="page">
          {error && <div className="alert error">{error}<button onClick={() => setError("")}>×</button></div>}
          {notice && <div className="alert success">{notice}<button onClick={() => setNotice("")}>×</button></div>}
          {busy && <div className="busy-line" />}
          {section === "Overview" && <Overview user={user} setSection={setSection} />}
          {section === "Resume analysis" && <ResumePanel token={token} run={run} setNotice={setNotice} />}
          {section === "Knowledge base" && <KnowledgePanel token={token} run={run} setNotice={setNotice} />}
          {section === "Mock interview" && <InterviewPanel token={token} run={run} setNotice={setNotice} />}
          {section === "Schedule" && <SchedulePanel token={token} run={run} setNotice={setNotice} />}
          {section === "Speech" && <SpeechPanel token={token} setNotice={setNotice} />}
          {section === "Settings" && <SettingsPanel token={token} run={run} />}
        </div>
      </main>
    </div>
  );
}

type Run = <T>(operation: () => Promise<T>) => Promise<T | undefined>;

function AuthScreen({ onLogin, run, busy, error }: { onLogin: (token: string) => void; run: Run; busy: boolean; error: string }) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (mode === "register") {
      const registered = await run(() => api<User>("/auth/register", "", { method: "POST", body: JSON.stringify({ email, password }) }));
      if (!registered) return;
    }
    const form = new URLSearchParams({ username: email, password });
    const login = await run(() => api<{ access_token: string }>("/auth/token", "", { method: "POST", body: form }));
    if (login) onLogin(login.access_token);
  }
  return <div className="auth-layout"><div className="auth-art"><div className="art-orb orb-one" /><div className="art-orb orb-two" /><div className="art-content"><div className="brand light"><span className="brand-icon">✦</span><b>Interview Guide</b></div><h1>Make your next<br />interview your best.</h1><p>Your personal AI workspace for confident career moves.</p><div className="art-tags"><span>Resume insights</span><span>Mock interviews</span><span>Knowledge powered</span></div></div></div><div className="auth-form-wrap"><form className="auth-card" onSubmit={submit}><div className="eyebrow">WELCOME BACK</div><h2>{mode === "login" ? "Sign in to your workspace" : "Create your workspace"}</h2><p>Use your account to continue your interview preparation.</p>{error && <div className="alert error">{error}</div>}<label>Email address<input type="email" required value={email} onChange={(event) => setEmail(event.target.value)} placeholder="you@example.com" /></label><label>Password<input type="password" required minLength={12} value={password} onChange={(event) => setPassword(event.target.value)} placeholder="At least 12 characters" /></label><button className="primary full" disabled={busy}>{busy ? "Please wait..." : mode === "login" ? "Sign in" : "Create account"} <span>→</span></button><div className="auth-switch">{mode === "login" ? "New to Interview Guide?" : "Already have an account?"}<button type="button" onClick={() => setMode(mode === "login" ? "register" : "login")}>{mode === "login" ? "Create account" : "Sign in"}</button></div></form></div></div>;
}

function Overview({ user, setSection }: { user: User; setSection: (s: string) => void }) {
  return <><div className="welcome-row"><div><div className="eyebrow">YOUR CAREER WORKSPACE</div><h1>Good to see you, {user.email.split("@")[0]}</h1><p>Pick up where you left off and get interview-ready.</p></div><span className="date-chip">{new Intl.DateTimeFormat(undefined, { dateStyle: "full" }).format(new Date())}</span></div><div className="stats-grid"><Stat icon="◈" label="Resume analyses" value="01" detail="Your profile, understood" /><Stat icon="▤" label="Knowledge bases" value="02" detail="Your learning library" /><Stat icon="◉" label="Mock interviews" value="03" detail="Practice with purpose" /><Stat icon="◷" label="Upcoming sessions" value="04" detail="Stay one step ahead" /></div><div className="section-heading"><div><h2>Continue your preparation</h2><p>Tools designed to help you show up with confidence.</p></div></div><div className="feature-grid"><Feature icon="◈" color="lavender" title="Analyze your resume" text="Understand your fit, uncover skill gaps, and get tailored improvement tips." action="Start analysis" onClick={() => setSection("Resume analysis")} /><Feature icon="▤" color="mint" title="Build your knowledge base" text="Bring your study material together and ask grounded questions." action="Explore knowledge" onClick={() => setSection("Knowledge base")} /><Feature icon="◉" color="peach" title="Practice an interview" text="Work through personalized questions, follow-ups, and feedback." action="Start practicing" onClick={() => setSection("Mock interview")} /></div><div className="bottom-grid"><div className="panel"><div className="panel-title"><div><h3>Preparation roadmap</h3><p>A simple plan for your next application</p></div><span className="pill">IN PROGRESS</span></div><div className="roadmap"><Road step="01" title="Review the role" text="Add a job description and understand what matters." done /><Road step="02" title="Practice your story" text="Build clear, evidence-based answers." /><Road step="03" title="Show up prepared" text="Review your notes before interview day." /></div></div><div className="quote-card"><div className="quote-mark">“</div><p>Confidence comes from preparation, not prediction.</p><span>YOUR PERSONAL INTERVIEW COACH</span></div></div></>;
}

function ResumePanel({ token, run, setNotice }: { token: string; run: Run; setNotice: (s: string) => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [jd, setJd] = useState("");
  const [jobs, setJobs] = useState<Analysis[]>([]);
  async function loadHistory() { const data = await run(() => api<Analysis[]>("/analyses", token)); if (data) setJobs(data); }
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!file) { setNotice("Choose a resume file first."); return; }
    const body = new FormData(); body.append("file", file); body.append("job_description", jd);
    const job = await run(() => api<Analysis>("/resumes", token, { method: "POST", body }));
    if (job) { setJobs((items) => [job, ...items]); setNotice("Resume uploaded. Analysis is queued."); }
  }
  useEffect(() => { void loadHistory(); }, []);
  async function downloadReport(id: string) {
    const blob = await run(() => api<Blob>(`/analyses/${id}/report`, token));
    if (blob) saveBlob(blob, `analysis-${id}.pdf`);
  }
  return <><PageHeading eyebrow="RESUME INTELLIGENCE" title="Resume analysis" subtitle="Understand how your experience aligns with the role you want." /><div className="content-grid"><form className="panel form-panel" onSubmit={submit}><h3>Start a new analysis</h3><p>Upload your resume and share the role description.</p><label>Resume file<input type="file" accept=".txt,.md,.pdf" onChange={(e) => setFile(e.target.files?.[0] || null)} /></label><label>Job description<textarea required rows={8} value={jd} onChange={(e) => setJd(e.target.value)} placeholder="Paste the job description here..." /></label><button className="primary" disabled={!file}>Upload and analyze <span>→</span></button></form><div className="panel"><div className="panel-title"><div><h3>Your analysis history</h3><p>Reports are private to your account.</p></div><button className="text-button" onClick={() => void loadHistory()}>Refresh</button></div><div className="list">{jobs.length ? jobs.map((job) => <div className="list-row" key={job.id}><div className="row-icon">◈</div><div className="row-copy"><b>{job.resume_filename}</b><small>{job.status}{job.error_message ? ` · ${job.error_message}` : ""}</small></div>{job.status === "completed" && <button className="secondary small-button" onClick={() => void downloadReport(job.id)}>PDF</button>}<button className="icon-button" title="Refresh" onClick={() => void loadHistory()}>↻</button></div>) : <Empty text="Your completed and in-progress analyses will appear here." />}</div></div></div></>;
}

function KnowledgePanel({ token, run, setNotice }: { token: string; run: Run; setNotice: (s: string) => void }) {
  const [bases, setBases] = useState<KnowledgeBase[]>([]);
  const [selected, setSelected] = useState("");
  const [name, setName] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<{ answer: string; sources: Array<{ label: string; filename: string; page_number: number | null; excerpt: string }> } | null>(null);
  async function loadBases() { const data = await run(() => api<KnowledgeBase[]>("/knowledge-bases", token)); if (data) { setBases(data); if (!selected && data[0]) setSelected(data[0].id); } }
  async function loadDocs(id = selected) { if (!id) return; const data = await run(() => api<KnowledgeDocument[]>(`/knowledge-bases/${id}/documents`, token)); if (data) setDocuments(data); }
  useEffect(() => { void loadBases(); }, []);
  useEffect(() => { if (selected) void loadDocs(selected); }, [selected]);
  async function createBase(event: FormEvent) {
    event.preventDefault();
    const base = await run(() => api<KnowledgeBase>("/knowledge-bases", token, { method: "POST", body: JSON.stringify({ name }) }));
    if (base) { setBases((items) => [base, ...items]); setSelected(base.id); setName(""); }
  }
  async function upload(event: FormEvent) {
    event.preventDefault();
    if (!file || !selected) return;
    const body = new FormData(); body.append("file", file);
    const doc = await run(() => api<KnowledgeDocument>(`/knowledge-bases/${selected}/documents`, token, { method: "POST", body }));
    if (doc) { setDocuments((items) => [doc, ...items]); setNotice("Document queued for parsing and embedding."); setFile(null); }
  }
  async function ask(event: FormEvent) {
    event.preventDefault();
    const result = await run(() => api<typeof answer>(`/knowledge-bases/${selected}/chat`, token, { method: "POST", body: JSON.stringify({ question }) }));
    if (result) setAnswer(result);
  }
  return <><PageHeading eyebrow="YOUR LEARNING LIBRARY" title="Knowledge base" subtitle="Upload notes and ask questions grounded in your own materials." /><div className="content-grid"><div className="stack"><form className="panel form-panel" onSubmit={createBase}><h3>Create a knowledge base</h3><div className="inline-form"><input required value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. System design" /><button className="primary">Create</button></div></form><div className="panel form-panel"><div className="panel-title"><div><h3>Documents</h3><p>PDF, Word, Markdown or text</p></div><button className="text-button" onClick={() => void loadDocs()}>Refresh</button></div><select value={selected} onChange={(e) => setSelected(e.target.value)}><option value="">Choose a knowledge base</option>{bases.map((base) => <option value={base.id} key={base.id}>{base.name}</option>)}</select><form onSubmit={upload}><input type="file" accept=".pdf,.docx,.txt,.md" onChange={(e) => setFile(e.target.files?.[0] || null)} /><button className="secondary" disabled={!selected || !file}>Upload document</button></form><div className="list compact">{documents.map((doc) => <div className="list-row" key={doc.id}><div className="row-icon">▤</div><div className="row-copy"><b>{doc.original_filename}</b><small>{doc.status}</small></div></div>)}</div></div></div><div className="panel form-panel"><h3>Ask your materials</h3><p>Answers include sources from your indexed documents.</p><form onSubmit={ask}><textarea rows={4} required value={question} onChange={(e) => setQuestion(e.target.value)} placeholder="What should I know about system design interviews?" /><button className="primary" disabled={!selected}>Ask knowledge base <span>→</span></button></form>{answer && <div className="answer-card"><h4>Answer</h4><p>{answer.answer}</p><h4>Sources</h4>{answer.sources.map((source) => <div className="source" key={source.label}><b>[{source.label}] {source.filename}{source.page_number ? ` · p. ${source.page_number}` : ""}</b><small>{source.excerpt}</small></div>)}</div>}</div></div></>;
}

function InterviewPanel({ token, run, setNotice }: { token: string; run: Run; setNotice: (s: string) => void }) {
  const [role, setRole] = useState("");
  const [jd, setJd] = useState("");
  const [resume, setResume] = useState("");
  const [maxQuestions, setMaxQuestions] = useState(5);
  const [interview, setInterview] = useState<Interview | null>(null);
  const [answer, setAnswer] = useState("");
  async function start(event: FormEvent) {
    event.preventDefault();
    const result = await run(() => api<Interview>("/interviews", token, { method: "POST", body: JSON.stringify({ role, job_description: jd, resume_text: resume, max_questions: maxQuestions }) }));
    if (result) setInterview(result);
  }
  async function submitAnswer(event: FormEvent) {
    event.preventDefault();
    if (!interview) return;
    const result = await run(() => api<Interview>(`/interviews/${interview.id}/answers`, token, { method: "POST", body: JSON.stringify({ answer }) }));
    if (result) { setInterview(result); setAnswer(""); }
  }
  async function finish() {
    if (!interview) return;
    const result = await run(() => api<Interview>(`/interviews/${interview.id}/finish`, token, { method: "POST" }));
    if (result) { setInterview(result); setNotice("Interview completed. Your assessment is ready."); }
  }
  async function downloadReport() {
    if (!interview) return;
    const blob = await run(() => api<Blob>(`/interviews/${interview.id}/report`, token));
    if (blob) saveBlob(blob, `interview-${interview.id}.pdf`);
  }
  return <><PageHeading eyebrow="PRACTICE WITH PURPOSE" title="Mock interview" subtitle="A personalized practice session with follow-up questions and feedback." />{!interview ? <form className="panel form-panel interview-start" onSubmit={start}><h3>Set up your practice session</h3><div className="two-col"><label>Role<input required value={role} onChange={(e) => setRole(e.target.value)} placeholder="AI Product Intern" /></label><label>Number of questions<input type="number" min={1} max={20} value={maxQuestions} onChange={(e) => setMaxQuestions(Number(e.target.value))} /></label></div><label>Job description<textarea required rows={5} value={jd} onChange={(e) => setJd(e.target.value)} /></label><label>Resume text<textarea required rows={6} value={resume} onChange={(e) => setResume(e.target.value)} /></label><button className="primary">Generate first question <span>→</span></button></form> : <div className="interview-layout"><div className="panel interview-panel"><div className="panel-title"><div><div className="eyebrow">PRACTICE SESSION</div><h2>{interview.role}</h2></div><span className={`pill ${interview.status === "active" ? "" : "green-pill"}`}>{interview.status}</span></div>{interview.status === "active" && <div className="current-question"><div className="eyebrow">QUESTION {interview.turns.length + 1}</div><p>{interview.current_question}</p></div>}<div className="turns">{interview.turns.map((turn, i) => <div className="turn-card" key={i}><b>Q{i + 1}. {turn.question}</b><p>{turn.answer}</p><div className="feedback"><strong>Score {String(turn.evaluation.score)}/100</strong><span>{(turn.evaluation.strengths as string[] | undefined)?.join(" · ")}</span></div></div>)}</div>{interview.status === "active" ? <><form onSubmit={submitAnswer} className="answer-form"><textarea required rows={5} value={answer} onChange={(e) => setAnswer(e.target.value)} placeholder="Type your answer..." /><button className="primary">Submit answer <span>→</span></button></form><button className="text-button" onClick={() => void finish()}>Finish interview early</button></> : <div className="report-card"><h3>Interview assessment</h3><div className="score">{String(interview.report?.overall_score ?? "—")}<small>/100</small></div><p>{String(interview.report?.summary || "")}</p><button className="secondary" onClick={() => void downloadReport()}>Download PDF report</button></div>}</div><div className="panel interview-aside"><h3>Session guide</h3><p>Take a moment to structure each answer. Your feedback is private and designed for practice.</p><div className="guide-point"><span>01</span><div><b>Be specific</b><small>Describe your own contribution.</small></div></div><div className="guide-point"><span>02</span><div><b>Show the impact</b><small>Include the outcome or lesson.</small></div></div></div></div>}</>;
}

function SchedulePanel({ token, run, setNotice }: { token: string; run: Run; setNotice: (s: string) => void }) {
  const [events, setEvents] = useState<ScheduleEvent[]>([]);
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [title, setTitle] = useState("");
  const [company, setCompany] = useState("");
  const [startsAt, setStartsAt] = useState("");
  async function load() { const result = await run(() => api<ScheduleEvent[]>("/schedule/events", token)); if (result) setEvents(result); }
  async function loadNotifications() { const result = await run(() => api<Notification[]>("/notifications", token)); if (result) setNotifications(result); }
  useEffect(() => { void load(); void loadNotifications(); }, []);
  async function create(event: FormEvent) {
    event.preventDefault();
    const result = await run(() => api<ScheduleEvent>("/schedule/events", token, { method: "POST", body: JSON.stringify({ title, company: company || null, starts_at: new Date(startsAt).toISOString(), timezone_name: Intl.DateTimeFormat().resolvedOptions().timeZone, reminder_minutes: 30 }) }));
    if (result) { setEvents((items) => [...items, result].sort((a, b) => a.starts_at.localeCompare(b.starts_at))); setTitle(""); setCompany(""); setStartsAt(""); setNotice("Interview added to your schedule."); }
  }
  async function cancel(id: string) {
    const result = await run(async () => {
      await api<void>(`/schedule/events/${id}`, token, { method: "DELETE" });
      return true;
    });
    if (result) setEvents((items) => items.filter((event) => event.id !== id));
  }
  async function exportCalendar() {
    const blob = await run(() => api<Blob>("/schedule/events.ics", token));
    if (blob) saveBlob(blob, "interview-schedule.ics");
  }
  async function markRead(id: string) {
    const result = await run(() => api<Notification>(`/notifications/${id}/read`, token, { method: "POST" }));
    if (result) setNotifications((items) => items.map((item) => item.id === id ? result : item));
  }
  return <><PageHeading eyebrow="STAY READY" title="Interview schedule" subtitle="Keep your upcoming conversations and reminders in one place." /><div className="content-grid"><form className="panel form-panel" onSubmit={create}><h3>Add an interview</h3><label>Event title<input required value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Technical interview" /></label><label>Company<input value={company} onChange={(e) => setCompany(e.target.value)} placeholder="Company name" /></label><label>Date and time<input required type="datetime-local" value={startsAt} onChange={(e) => setStartsAt(e.target.value)} /></label><button className="primary">Add to schedule <span>→</span></button></form><div className="panel"><div className="panel-title"><div><h3>Upcoming interviews</h3><p>Reminders appear in your notifications.</p></div><button className="text-button" onClick={() => void exportCalendar()}>Export .ics</button></div><div className="list">{events.map((event) => <div className="list-row" key={event.id}><div className="row-icon">◷</div><div className="row-copy"><b>{event.title}{event.company ? ` · ${event.company}` : ""}</b><small>{new Date(event.starts_at).toLocaleString()}{event.meeting_url ? ` · ${event.meeting_url}` : ""}</small></div><button className="icon-button" title="Cancel event" onClick={() => void cancel(event.id)}>×</button></div>)}{!events.length && <Empty text="Your scheduled interviews will show up here." />}</div></div><div className="panel notification-panel"><div className="panel-title"><div><h3>Reminders and notifications</h3><p>In-app reminders from the background scheduler.</p></div><button className="text-button" onClick={() => void loadNotifications()}>Refresh</button></div><div className="list">{notifications.map((item) => <div className="list-row" key={item.id}><div className="row-icon">{item.read_at ? "✓" : "!"}</div><div className="row-copy"><b>{item.title}</b><small>{item.body}</small></div>{!item.read_at && <button className="text-button" onClick={() => void markRead(item.id)}>Mark read</button>}</div>)}{!notifications.length && <Empty text="No reminders yet. The reminder worker creates one when an interview is approaching." />}</div></div></div></>;
}

function SpeechPanel({ token, setNotice }: { token: string; setNotice: (s: string) => void }) {
  type SpeechMessage = { role: "candidate" | "interviewer"; text: string };
  const [role, setRole] = useState("Software Engineer");
  const [context, setContext] = useState("");
  const [status, setStatus] = useState("Disconnected");
  const [partial, setPartial] = useState("");
  const [messages, setMessages] = useState<SpeechMessage[]>([]);
  const socketRef = useRef<WebSocket | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const contextRef = useRef<AudioContext | null>(null);
  const sourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);
  const nextPlayTimeRef = useRef(0);

  function stopMicrophone() {
    processorRef.current?.disconnect();
    sourceRef.current?.disconnect();
    streamRef.current?.getTracks().forEach((track) => track.stop());
    processorRef.current = null;
    sourceRef.current = null;
    streamRef.current = null;
    void contextRef.current?.close();
    contextRef.current = null;
    setStatus("Connected");
  }

  function disconnect() {
    const socket = socketRef.current;
    if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: "stop" }));
    socket?.close();
    socketRef.current = null;
    stopMicrophone();
    setStatus("Disconnected");
  }

  useEffect(() => () => {
    socketRef.current?.close();
    processorRef.current?.disconnect();
    sourceRef.current?.disconnect();
    streamRef.current?.getTracks().forEach((track) => track.stop());
    void contextRef.current?.close();
  }, []);

  function startSession() {
    const apiPath = `${API.replace(/\/$/, "")}/speech/realtime`;
    const url = new URL(apiPath, window.location.origin);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(url, ["interview-guide", `auth.${token}`]);
    socketRef.current = socket;
    setStatus("Connecting");
    setMessages([]);
    setPartial("");
    socket.onopen = () => socket.send(JSON.stringify({ type: "start", role, context }));
    socket.onmessage = (event: MessageEvent<string>) => {
      let message: { type?: string; text?: string; audio?: string; message?: string };
      try {
        message = JSON.parse(event.data) as typeof message;
      } catch {
        setStatus("Could not read a speech server response.");
        return;
      }
      if (message.type === "ready") {
        setStatus("Connected");
        setNotice("Realtime speech interview connected.");
      } else if (message.type === "transcript.partial") {
        setPartial(message.text || "");
      } else if (message.type === "transcript.final" && message.text) {
        setPartial("");
        setMessages((items) => [...items, { role: "candidate", text: message.text! }]);
      } else if (message.type === "assistant.text" && message.text) {
        setMessages((items) => [...items, { role: "interviewer", text: message.text! }]);
      } else if (message.type === "assistant.audio" && message.audio) {
        const audioContext = contextRef.current;
        if (!audioContext) return;
        const raw = Uint8Array.from(atob(message.audio), (character) => character.charCodeAt(0));
        const sampleCount = Math.floor(raw.byteLength / 2);
        if (!sampleCount) return;
        const view = new DataView(raw.buffer, raw.byteOffset, raw.byteLength);
        const buffer = audioContext.createBuffer(1, sampleCount, 24000);
        const samples = buffer.getChannelData(0);
        for (let i = 0; i < sampleCount; i += 1) samples[i] = view.getInt16(i * 2, true) / 32768;
        const source = audioContext.createBufferSource();
        source.buffer = buffer;
        source.connect(audioContext.destination);
        const playAt = Math.max(audioContext.currentTime, nextPlayTimeRef.current);
        source.start(playAt);
        nextPlayTimeRef.current = playAt + buffer.duration;
      } else if (message.type === "error") {
        setNotice(message.message || "Realtime speech failed.");
      }
    };
    socket.onerror = () => setStatus("Speech connection failed.");
    socket.onclose = () => {
      socketRef.current = null;
      stopMicrophone();
      setStatus("Disconnected");
    };
  }

  async function startMicrophone() {
    const socket = socketRef.current;
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      setNotice("Connect the realtime interview before enabling your microphone.");
      return;
    }
    let acquiredStream: MediaStream | undefined;
    let createdContext: AudioContext | undefined;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
      });
      acquiredStream = stream;
      const audioContext = new AudioContext({ sampleRate: 16000 });
      createdContext = audioContext;
      await audioContext.resume();
      const source = audioContext.createMediaStreamSource(stream);
      const processor = audioContext.createScriptProcessor(4096, 1, 1);
      processor.onaudioprocess = (event) => {
        const input = event.inputBuffer.getChannelData(0);
        const targetLength = Math.max(1, Math.floor(input.length * 16000 / audioContext.sampleRate));
        const pcm = new Int16Array(targetLength);
        for (let i = 0; i < targetLength; i += 1) {
          const sample = input[Math.min(input.length - 1, Math.floor(i * audioContext.sampleRate / 16000))];
          pcm[i] = Math.max(-1, Math.min(1, sample)) < 0
            ? Math.max(-32768, Math.min(32767, sample * 32768))
            : Math.max(-32768, Math.min(32767, sample * 32767));
        }
        event.outputBuffer.getChannelData(0).fill(0);
        if (socket.readyState === WebSocket.OPEN) socket.send(pcm.buffer);
      };
      source.connect(processor);
      processor.connect(audioContext.destination);
      streamRef.current = stream;
      contextRef.current = audioContext;
      sourceRef.current = source;
      processorRef.current = processor;
      setStatus("Listening");
    } catch {
      acquiredStream?.getTracks().forEach((track) => track.stop());
      void createdContext?.close();
      setNotice("Microphone access is unavailable. Allow microphone access and try again.");
    }
  }

  return <><PageHeading eyebrow="SPEAK AND LISTEN" title="Realtime voice interview" subtitle="Speak naturally with a live AI interviewer. Audio is sent to the configured DashScope realtime speech service." /><div className="content-grid"><form className="panel form-panel" onSubmit={(event: FormEvent) => { event.preventDefault(); }}><div className="feature-icon lavender">◉</div><h3>Interview setup</h3><label>Target role<input value={role} onChange={(event) => setRole(event.target.value)} maxLength={120} disabled={status !== "Disconnected"} /></label><label>Context or job requirements<textarea rows={5} value={context} onChange={(event) => setContext(event.target.value)} maxLength={3000} placeholder="Optional role details to tailor the interview..." disabled={status !== "Disconnected"} /></label><div className="two-col"><button className="primary" type="button" onClick={startSession} disabled={status !== "Disconnected"}>Connect <span>→</span></button><button className="secondary" type="button" onClick={disconnect} disabled={status === "Disconnected"}>Disconnect</button></div><div className="two-col"><button className="secondary" type="button" onClick={() => void startMicrophone()} disabled={status !== "Connected"}>Enable microphone</button><button className="text-button" type="button" onClick={stopMicrophone} disabled={status !== "Listening"}>Mute microphone</button></div><p className="muted">Status: {status}</p></form><div className="panel"><div className="panel-title"><div><h3>Conversation</h3><p>Qwen realtime ASR and TTS · 16 kHz input / 24 kHz output</p></div></div><div className="turns">{messages.map((message, index) => <div className="turn-card" key={`${message.role}-${index}`}><b>{message.role === "candidate" ? "You" : "Interviewer"}</b><p>{message.text}</p></div>)}{partial && <div className="turn-card"><b>You (recognizing)</b><p>{partial}</p></div>}{!messages.length && !partial && <Empty text="Connect and enable your microphone to begin speaking." />}</div></div></div><div className="notice-card"><b>Local speech configuration</b><span>Requires AI_BAILIAN_API_KEY in .env, microphone permission, and an eligible DashScope account with the configured Qwen realtime ASR and TTS models enabled.</span></div></>;
}

function SettingsPanel({ token, run }: { token: string; run: Run }) {
  type SettingsData = {
    active_provider: string;
    providers: Array<{ name: string; model: string; base_url: string; configured: boolean; active: boolean }>;
    embedding: { provider: string; base_url: string; model: string; dimensions: number; configured: boolean };
    speech: { provider: string; configured: boolean; asr_model: string; tts_model: string; voice: string };
  };
  const [settings, setSettings] = useState<SettingsData | null>(null);
  async function load() {
    const result = await run(() => api<SettingsData>("/settings/models", token));
    if (result) setSettings(result);
  }
  useEffect(() => { void load(); }, []);
  return <><PageHeading eyebrow="YOUR CONFIGURATION" title="AI service configuration" subtitle="Local Docker uses credentials and model providers configured in the project .env file." /><div className="panel"><div className="panel-title"><div><h3>Chat model providers</h3><p>Configure providers with ACTIVE_CHAT_PROVIDER and MODEL_PROVIDERS.</p></div><button className="text-button" onClick={() => void load()}>Refresh</button></div>{settings?.providers.map((item) => <div className="list-row" key={item.name}><div className="row-icon">✦</div><div className="row-copy"><b>{item.name} {item.active && <span className="pill green-pill">ACTIVE</span>}</b><small>{item.model} · {item.base_url}</small></div><span className={item.configured ? "configured" : "not-configured"}>{item.configured ? "Configured" : "Key missing"}</span></div>)}{!settings?.providers.length && <Empty text="No chat model provider is configured." />}</div><div className="panel settings-provider-panel"><div className="panel-title"><div><h3>Embedding provider</h3><p>Configured independently through .env.</p></div><span className={settings?.embedding.configured ? "configured" : "not-configured"}>{settings?.embedding.configured ? "Configured" : "Key missing"}</span></div>{settings?.embedding && <div className="list-row"><div className="row-icon">⌕</div><div className="row-copy"><b>{settings.embedding.provider} · {settings.embedding.model}</b><small>{settings.embedding.base_url} · {settings.embedding.dimensions} dimensions</small></div></div>}</div><div className="panel settings-provider-panel"><div className="panel-title"><div><h3>Realtime speech provider</h3><p>Alibaba Cloud Bailian / DashScope configuration.</p></div><span className={settings?.speech.configured ? "configured" : "not-configured"}>{settings?.speech.configured ? "Configured" : "Key missing"}</span></div>{settings?.speech && <div className="list-row"><div className="row-icon">♫</div><div className="row-copy"><b>{settings.speech.asr_model} + {settings.speech.tts_model}</b><small>Voice: {settings.speech.voice}</small></div></div>}</div><div className="notice-card"><b>Credentials stay in .env</b><span>Users do not enter API keys in the website. Configure separate chat, embedding, and AI_BAILIAN_API_KEY credentials locally, then restart the relevant Docker services.</span></div></>;
}

function PageHeading({ eyebrow, title, subtitle }: { eyebrow: string; title: string; subtitle: string }) { return <div className="page-heading"><div className="eyebrow">{eyebrow}</div><h1>{title}</h1><p>{subtitle}</p></div>; }
function Stat({ icon, label, value, detail }: { icon: string; label: string; value: string; detail: string }) { return <div className="stat-card"><div className="stat-top"><span>{label}</span><i>{icon}</i></div><strong>{value}</strong><small>{detail}</small></div>; }
function Feature({ icon, color, title, text, action, onClick }: { icon: string; color: string; title: string; text: string; action: string; onClick: () => void }) { return <div className="feature-card"><div className={`feature-icon ${color}`}>{icon}</div><h3>{title}</h3><p>{text}</p><button className="text-button" onClick={onClick}>{action} <span>→</span></button></div>; }
function Road({ step, title, text, done = false }: { step: string; title: string; text: string; done?: boolean }) { return <div className="road-item"><span className={done ? "road-number done" : "road-number"}>{done ? "✓" : step}</span><div><b>{title}</b><small>{text}</small></div><span className={done ? "road-state complete" : "road-state"}>{done ? "DONE" : "NEXT"}</span></div>; }
function Empty({ text }: { text: string }) { return <div className="empty-state"><span>✦</span><p>{text}</p></div>; }
function iconFor(item: string) { return ({ Overview: "⌂", "Resume analysis": "◈", "Knowledge base": "▤", "Mock interview": "◉", Schedule: "◷", Speech: "♫", Settings: "⚙" } as Record<string, string>)[item]; }
function saveBlob(blob: Blob, filename: string) { const url = URL.createObjectURL(blob); const link = document.createElement("a"); link.href = url; link.download = filename; link.click(); URL.revokeObjectURL(url); }

export default App;
