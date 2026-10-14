import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ChangeEvent, FormEvent } from 'react';
import type { Account, AccountInput, AccountStatus, AuditEvent, BrowserProfile, ProfileInput } from '../shared/contracts';

type Page = 'dashboard' | 'accounts' | 'profiles' | 'audit';
type Feedback = { type: 'error' | 'success'; message: string } | null;
type AccountForm = AccountInput & { id?: string };
type ProfileForm = ProfileInput & { id?: string };

const emptyAccount = (): AccountForm => ({
  provider: '', displayName: '', parentAccountId: null, profileId: null, status: 'draft',
});
const emptyProfile = (): ProfileForm => ({ label: '', homeUrl: '' });
const statusName: Record<AccountStatus, string> = {
  draft: 'Rascunho', ready: 'Preparada', paused: 'Pausada',
};
const nav: Array<{ key: Page; name: string; icon: string; subtitle: string }> = [
  { key: 'dashboard', name: 'Visão geral', icon: '◈', subtitle: 'Resumo' },
  { key: 'accounts', name: 'Contas', icon: '▣', subtitle: 'Mãe e filha' },
  { key: 'profiles', name: 'Perfis', icon: '▤', subtitle: 'Sessões isoladas' },
  { key: 'audit', name: 'Auditoria', icon: '≡', subtitle: 'Histórico' },
];
const pageTitle: Record<Page, string> = {
  dashboard: 'Visão geral', accounts: 'Gerenciar contas',
  profiles: 'Perfis de navegador', audit: 'Histórico de ações',
};
const fmt = (value: string) => {
  try { return new Date(value).toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' }); }
  catch { return value; }
};
const errorText = (error: unknown): string =>
  error instanceof Error ? error.message.replace(/^Error invoking remote method '[^']+': Error: /, '') : 'Operação não concluída.';

function parseCsv(text: string): Array<Record<string, string>> {
  const rows: string[][] = [], current: string[] = [];
  let cell = '', quotes = false;
  const sep = (text.split(/\r?\n/)[0] || '').includes(';') ? ';' : ',';
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (ch === '"') {
      if (quotes && text[i + 1] === '"') { cell += '"'; i++; }
      else quotes = !quotes;
    } else if (ch === sep && !quotes) {
      current.push(cell); cell = '';
    } else if ((ch === '\n' || ch === '\r') && !quotes) {
      if (ch === '\r' && text[i + 1] === '\n') i++;
      current.push(cell); cell = '';
      if (current.some(v => v.trim())) rows.push([...current]);
      current.length = 0;
    } else cell += ch;
  }
  if (quotes) throw new Error('CSV com aspas não fechadas.');
  current.push(cell);
  if (current.some(v => v.trim())) rows.push(current);
  const headers = (rows.shift() || []).map(h => h.trim().replace(/^\uFEFF/, ''));
  if (!headers.includes('provider') || !headers.includes('displayName')) {
    throw new Error('CSV deve conter as colunas provider e displayName.');
  }
  return rows.map(row => Object.fromEntries(headers.map((h,i) => [h, (row[i] || '').trim()])));
}

export default function App() {
  const [page, setPage] = useState<Page>('dashboard');
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [profiles, setProfiles] = useState<BrowserProfile[]>([]);
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [feedback, setFeedback] = useState<Feedback>(null);
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState('all');
  const [accountForm, setAccountForm] = useState<AccountForm | null>(null);
  const [profileForm, setProfileForm] = useState<ProfileForm | null>(null);
  const importRef = useRef<HTMLInputElement>(null);

  const reload = useCallback(async () => {
    const [allAccounts, allProfiles, allEvents] = await Promise.all([
      window.orbita.listAccounts(), window.orbita.listProfiles(), window.orbita.listAudit(),
    ]);
    setAccounts(allAccounts); setProfiles(allProfiles); setEvents(allEvents);
  }, []);

  useEffect(() => {
    void reload().catch(err => setFeedback({ type: 'error', message: errorText(err) }))
      .finally(() => setLoading(false));
  }, [reload]);

  async function execute<T>(work: () => Promise<T>, success: string): Promise<T | undefined> {
    if (busy) return;
    setBusy(true); setFeedback(null);
    try {
      const result = await work();
      await reload();
      setFeedback({ type: 'success', message: success });
      return result;
    } catch (err) {
      setFeedback({ type: 'error', message: errorText(err) });
    } finally { setBusy(false); }
  }

  async function saveAccount(e: FormEvent) {
    e.preventDefault();
    if (!accountForm) return;
    const data = accountForm;
    const result = await execute(
      () => data.id ? window.orbita.updateAccount({ ...data, id: data.id })
        : window.orbita.createAccount(data),
      data.id ? 'Conta atualizada localmente.' : 'Conta registrada localmente.',
    );
    if (result) setAccountForm(null);
  }
  async function saveProfile(e: FormEvent) {
    e.preventDefault();
    if (!profileForm) return;
    const data = profileForm;
    const result = await execute(
      () => data.id ? window.orbita.updateProfile({ ...data, id: data.id })
        : window.orbita.createProfile(data),
      data.id ? 'Perfil atualizado.' : 'Perfil de navegador criado.',
    );
    if (result) setProfileForm(null);
  }
  async function onImport(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    if (file.size > 300_000) { setFeedback({ type: 'error', message: 'Arquivo muito grande (máximo 300 KB).' }); return; }
    try {
      const raw = await file.text();
      const data: unknown = file.name.toLowerCase().endsWith('.json') ? JSON.parse(raw) : parseCsv(raw);
      if (!Array.isArray(data)) throw new Error('O arquivo precisa conter uma lista de contas.');
      // Imported credentials are never accepted or stored.
      const rows = data.map((row: Record<string, unknown>) => ({
        provider: row.provider, displayName: row.displayName,
        parentAccountId: row.parentAccountId || null,
        profileId: row.profileId || null,
        status: row.status || 'draft',
      })) as AccountInput[];
      await execute(() => window.orbita.importAccounts(rows), 'Contas importadas localmente.');
    } catch (err) { setFeedback({ type: 'error', message: errorText(err) }); }
  }
  const filtered = useMemo(() => accounts.filter(acc => {
    const matchesText = [acc.displayName, acc.provider].join(' ').toLowerCase().includes(search.toLowerCase());
    return matchesText && (filter === 'all' || (filter === 'parent' ? !acc.parentAccountId
      : filter === 'child' ? Boolean(acc.parentAccountId) : acc.status === filter));
  }), [accounts, search, filter]);

  const parents = accounts.filter(acc => !acc.parentAccountId).length;
  const children = accounts.length - parents;
  const byAccount = (key: string | null) => accounts.find(acc => acc.id === key)?.displayName ?? '—';
  const byProfile = (key: string | null) => profiles.find(profile => profile.id === key)?.label ?? 'Sem perfil';

  return <div className="shell">
    <aside className="sidebar">
      <div className="logo"><div className="logo-mark">◎</div><div><strong>ÓRBITA</strong><span>WORKSPACE</span></div></div>
      <div className="sidebar-group">ESPAÇO DE TRABALHO</div>
      <nav aria-label="Menu principal">
        {nav.map(item => <button key={item.key} type="button"
          onClick={() => { setPage(item.key); setFeedback(null); }}
          className={'nav-link ' + (page === item.key ? 'selected' : '')}
          aria-current={page === item.key ? 'page' : undefined}>
          <span className="nav-icon" aria-hidden="true">{item.icon}</span>
          <span>{item.name}<small>{item.subtitle}</small></span>
          {page === item.key && <span className="nav-chevron" aria-hidden="true">›</span>}
        </button>)}
      </nav>
      <div className="sidebar-bottom"><span className="local-indicator" />
        <div><b>Dados locais</b><small>Sem integrações externas</small></div></div>
      <div className="sidebar-version">FASE 01 · CONTAS E PERFIS</div>
    </aside>

    <div className="main-column">
      <header className="topbar">
        <div className="breadcrumb">WORKSPACE <span>/</span> <b>{pageTitle[page]}</b></div>
        <div className="topbar-right"><span className="environment-pill">● Aplicativo local</span><span className="topbar-avatar">OW</span></div>
      </header>
      <main className="workspace">
        <div className="page-heading">
          <div><div className="eyebrow">PAINEL DE CONTROLE / FASE 01</div>
            <h1>{pageTitle[page]}</h1>
            <p>{page === 'dashboard' ? 'Organize contas, relacionamentos e perfis de navegação em um só lugar.'
              : page === 'accounts' ? 'Registros locais organizados por provedor, conta mãe e conta filha.'
              : page === 'profiles' ? 'Janelas independentes com cookies e armazenamento separados.'
              : 'Registro local de alterações, aberturas e ações executadas.'}</p>
          </div>
          <div className="page-actions">
            {page === 'accounts' && <>
              <input ref={importRef} hidden type="file" accept=".csv,.json" onChange={onImport} />
              <button className="button ghost" onClick={() => importRef.current?.click()} disabled={busy}>↥ Importar</button>
              <button className="button primary" onClick={() => setAccountForm(emptyAccount())}>＋ Nova conta</button>
            </>}
            {page === 'profiles' && <button className="button primary" onClick={() => setProfileForm(emptyProfile())}>＋ Novo perfil</button>}
            {page === 'dashboard' && <button className="button primary" onClick={() => { setPage('accounts'); setAccountForm(emptyAccount()); }}>＋ Criar conta</button>}
          </div>
        </div>

        {feedback && <div role="alert" className={'feedback ' + feedback.type}>
          <span>{feedback.type === 'success' ? '✓' : '!'}</span>{feedback.message}
          <button type="button" aria-label="Fechar aviso" onClick={() => setFeedback(null)}>×</button>
        </div>}
        {loading ? <div className="empty-card">Carregando banco local…</div> : <>
          {page === 'dashboard' && <>
            <section className="stat-grid" aria-label="Resumo do sistema">
              {[{ label:'Contas cadastradas',value:accounts.length,icon:'▣',detail:'Registros armazenados localmente' },
                { label:'Contas mãe',value:parents,icon:'◈',detail:'Registros sem conta principal' },
                { label:'Contas filha',value:children,icon:'◇',detail:'Relacionamentos locais' },
                { label:'Perfis isolados',value:profiles.length,icon:'▤',detail:'Partições independentes' }].map(card =>
                <article className="stat-card" key={card.label}>
                  <span className="stat-icon">{card.icon}</span><div className="stat-label">{card.label}</div>
                  <div className="stat-value">{card.value.toString().padStart(2,'0')}</div>
                  <div className="stat-detail">{card.detail}</div>
                </article>)}
            </section>
            <div className="dashboard-grid">
              <section className="panel"><div className="panel-head"><div><h2>Contas recentes</h2><p>Últimas contas cadastradas</p></div><button className="text-link" onClick={() => setPage('accounts')}>Ver todas →</button></div>
                {accounts.length ? <div className="list-rows">{accounts.slice(0,5).map(a =>
                  <div className="account-line" key={a.id}><span className="round-icon">▣</span><div><b>{a.displayName}</b><small>{a.provider} · {a.parentAccountId ? 'Conta filha' : 'Conta mãe'}</small></div><span className={'status '+a.status}>{statusName[a.status]}</span></div>)}</div>
                  : <Empty icon="▣" title="Nenhuma conta cadastrada" description="Crie uma conta mãe ou filha para começar." />}
              </section>
              <section className="panel"><div className="panel-head"><div><h2>Atividade recente</h2><p>Eventos auditados no dispositivo</p></div><button className="text-link" onClick={() => setPage('audit')}>Abrir histórico →</button></div>
                {events.length ? <div className="list-rows">{events.slice(0,5).map(e =>
                  <div className="audit-line" key={e.id}><span className="event-dot" /><div><b>{e.action}</b><small>{fmt(e.timestamp)}</small></div><span className="audit-result">{e.result}</span></div>)}</div>
                  : <Empty icon="≡" title="Sem eventos ainda" description="As alterações locais aparecerão aqui." />}
              </section>
            </div>
            <div className="info-strip"><span>ⓘ</span><div><b>Operação local, sem integração com plataformas</b><p>Registros e vínculos não representam criação de contas em sites externos. Os perfis abrem páginas convencionais para navegação manual.</p></div></div>
          </>}

          {page === 'accounts' && <section className="panel">
            <div className="panel-head"><div><h2>Diretório de contas</h2><p>{accounts.length} registros locais · relação mãe/filha</p></div>
              <span className="muted-count">{filtered.length} exibidas</span></div>
            <div className="filters"><label className="search-field"><span>⌕</span>
              <input aria-label="Buscar contas" placeholder="Buscar por nome ou provedor" value={search} onChange={e=>setSearch(e.target.value)}/></label>
              <select aria-label="Filtrar contas" value={filter} onChange={e=>setFilter(e.target.value)}>
                <option value="all">Todas as contas</option><option value="parent">Contas mãe</option>
                <option value="child">Contas filha</option><option value="draft">Rascunhos</option>
                <option value="ready">Preparadas</option><option value="paused">Pausadas</option>
              </select></div>
            {filtered.length ? <div className="table-scroll"><table className="records-table">
              <thead><tr><th>Conta</th><th>Provedor</th><th>Relacionamento</th><th>Perfil</th><th>Status</th><th>Ações</th></tr></thead>
              <tbody>{filtered.map(a=><tr key={a.id}>
                <td><div className="primary-cell"><span className="row-icon">◉</span><div><b>{a.displayName}</b><small>{fmt(a.createdAt)}</small></div></div></td>
                <td>{a.provider}</td><td>{a.parentAccountId ? byAccount(a.parentAccountId) : <span className="parent-label">Conta mãe</span>}</td>
                <td>{byProfile(a.profileId)}</td><td><span className={'status '+a.status}>{statusName[a.status]}</span></td>
                <td className="actions-cell"><button className="mini-button" title="Editar conta" onClick={()=>setAccountForm({...a})}>Editar</button>
                  <button className="mini-button danger" disabled={busy} onClick={()=>{
                    if(window.confirm('Excluir registro local desta conta?')) void execute(()=>window.orbita.deleteAccount(a.id),'Conta removida.');
                  }}>Excluir</button></td>
              </tr>)}</tbody></table></div>
              : <Empty icon="▣" title={search||filter!=='all'?'Nenhuma conta encontrada':'Seu diretório está vazio'}
                description="Crie uma conta ou importe registros próprios em CSV/JSON (sem senhas)." />}
          </section>}

          {page === 'profiles' && <section className="panel">
            <div className="panel-head"><div><h2>Perfis de navegação</h2><p>Uma partição de armazenamento independente para cada perfil</p></div>
              <span className="muted-count">{profiles.length} perfis</span></div>
            <div className="profiles-grid">{profiles.length ? profiles.map(p =>
              <article className="profile-card" key={p.id}>
                <div className="profile-card-top"><span className="profile-illustration">◎</span><span className="badge-isolation">ISOLADO</span></div>
                <h3>{p.label}</h3><p className="profile-url" title={p.homeUrl}>{p.homeUrl}</p>
                <div className="profile-metadata"><span>Partição privada</span><b>{p.id.slice(0,8)}…</b></div>
                <div className="profile-actions">
                  <button className="button primary" disabled={busy} onClick={()=>{void execute(()=>window.orbita.openProfile(p.id),'Janela de perfil solicitada.');}}>Abrir navegador ↗</button>
                  <button className="button ghost" disabled={busy} onClick={()=>setProfileForm({...p})}>Editar</button>
                </div>
                <div className="profile-destructive">
                  <button disabled={busy} onClick={()=>void execute(()=>window.orbita.closeProfile(p.id),'Fechamento solicitado.')}>Fechar</button>
                  <button disabled={busy} onClick={()=>{
                    if(window.confirm('Apagar cookies, armazenamento e cache deste perfil? Esta ação não pode ser desfeita.'))
                      void execute(()=>window.orbita.clearProfile(p.id),'Armazenamento do perfil limpo.');
                  }}>Limpar dados</button>
                  <button className="red" disabled={busy} onClick={()=>{
                    if(window.confirm('Remover perfil e limpar seus dados? Contas vinculadas perderão a associação.'))
                      void execute(()=>window.orbita.deleteProfile(p.id),'Perfil removido.');
                  }}>Excluir</button>
                </div>
              </article>) : <div className="profiles-empty"><Empty icon="▤" title="Nenhum perfil criado" description="Crie o primeiro perfil para abrir uma sessão de navegador independente." /></div>}
            </div></section>}

          {page === 'audit' && <section className="panel">
            <div className="panel-head"><div><h2>Registro de auditoria</h2><p>Ações locais gravadas em SQLite, sem credenciais ou tokens</p></div><span className="muted-count">Últimos 150</span></div>
            {events.length ? <div className="table-scroll"><table className="records-table">
              <thead><tr><th>Data</th><th>Ação</th><th>Entidade</th><th>Referência</th><th>Resultado</th></tr></thead>
              <tbody>{events.map(ev=><tr key={ev.id}><td>{fmt(ev.timestamp)}</td><td><b>{ev.action}</b></td>
                <td>{ev.entityType}</td><td className="mono">{ev.entityId.length>24?ev.entityId.slice(0,8)+'…':ev.entityId}</td>
                <td><span className="status ready">{ev.result}</span></td></tr>)}</tbody>
            </table></div>:<Empty icon="≡" title="Nenhum evento" description="Operações locais serão registradas automaticamente." />}
          </section>}
        </>}
        <footer className="workspace-footer">ÓRBITA WORKSPACE <span>·</span> FASE 01 <span>·</span> DADOS LOCAIS E NAVEGAÇÃO MANUAL</footer>
      </main>
    </div>

    {accountForm && <div className="modal-backdrop" role="presentation" onMouseDown={e=>{if(e.target===e.currentTarget)setAccountForm(null);}}>
      <section className="modal" role="dialog" aria-modal="true" aria-labelledby="account-modal-title">
        <div className="modal-head"><div><div className="eyebrow">DIRETÓRIO / CONTAS</div>
          <h2 id="account-modal-title">{accountForm.id?'Editar conta':'Nova conta'}</h2>
          <p>Cadastro local, sem chamada a provedor externo.</p></div>
          <button className="close-modal" type="button" aria-label="Fechar" onClick={()=>setAccountForm(null)}>×</button>
        </div>
        <form onSubmit={saveAccount} className="modal-body">
          <label>Nome da conta<input required maxLength={120} autoFocus placeholder="Ex.: Conta principal" value={accountForm.displayName}
            onChange={e=>setAccountForm({...accountForm, displayName:e.target.value})}/></label>
          <label>Provedor ou plataforma<input required maxLength={80} placeholder="Nome de referência" value={accountForm.provider}
            onChange={e=>setAccountForm({...accountForm, provider:e.target.value})}/></label>
          <div className="form-grid">
            <label>Conta mãe<select value={accountForm.parentAccountId??''}
              onChange={e=>setAccountForm({...accountForm,parentAccountId:e.target.value||null})}>
              <option value="">Nenhuma — conta mãe</option>
              {accounts.filter(a=>a.id!==accountForm.id).map(a=><option key={a.id} value={a.id}>{a.displayName}</option>)}
            </select></label>
            <label>Perfil de navegador<select value={accountForm.profileId??''}
              onChange={e=>setAccountForm({...accountForm,profileId:e.target.value||null})}>
              <option value="">Sem perfil associado</option>{profiles.map(p=><option key={p.id} value={p.id}>{p.label}</option>)}
            </select></label>
          </div>
          <label>Estado local<select value={accountForm.status ?? 'draft'}
            onChange={e=>setAccountForm({...accountForm,status:e.target.value as AccountStatus})}>
            <option value="draft">Rascunho</option><option value="ready">Preparada</option><option value="paused">Pausada</option>
          </select></label>
          <p className="form-help">Nenhuma conta é criada automaticamente em sites ou plataformas.</p>
          <div className="modal-actions"><button type="button" className="button ghost" onClick={()=>setAccountForm(null)}>Cancelar</button>
            <button type="submit" className="button primary" disabled={busy}>{busy?'Salvando...':'Salvar conta'}</button></div>
        </form>
      </section></div>}

    {profileForm && <div className="modal-backdrop" role="presentation" onMouseDown={e=>{if(e.target===e.currentTarget)setProfileForm(null);}}>
      <section className="modal" role="dialog" aria-modal="true" aria-labelledby="profile-modal-title">
        <div className="modal-head"><div><div className="eyebrow">SESSÕES / NAVEGAÇÃO</div>
          <h2 id="profile-modal-title">{profileForm.id?'Editar perfil':'Novo perfil'}</h2>
          <p>Janela separada, com cookies e dados próprios.</p></div>
          <button type="button" className="close-modal" aria-label="Fechar" onClick={()=>setProfileForm(null)}>×</button>
        </div>
        <form onSubmit={saveProfile} className="modal-body">
          <label>Identificação do perfil<input required maxLength={80} autoFocus placeholder="Ex.: Meu perfil 01" value={profileForm.label}
            onChange={e=>setProfileForm({...profileForm,label:e.target.value})}/></label>
          <label>URL inicial (opcional)<input placeholder="exemplo.com ou https://exemplo.com" value={profileForm.homeUrl==='about:blank'?'':profileForm.homeUrl}
            onChange={e=>setProfileForm({...profileForm,homeUrl:e.target.value})}/></label>
          <p className="form-help">HTTPS é aplicado a domínios sem protocolo. CAPTCHA e autenticação ficam sob controle do usuário.</p>
          <div className="modal-actions"><button type="button" className="button ghost" onClick={()=>setProfileForm(null)}>Cancelar</button>
            <button type="submit" className="button primary" disabled={busy}>{busy?'Salvando...':'Salvar perfil'}</button></div>
        </form>
      </section></div>}
  </div>;
}

function Empty({icon,title,description}:{icon:string;title:string;description:string}) {
  return <div className="empty-state"><div className="empty-icon">{icon}</div><b>{title}</b><p>{description}</p></div>;
}
