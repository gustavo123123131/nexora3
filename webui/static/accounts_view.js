/* Nexora Contas: read-only local browser profile directory.
 * Only "Abrir Conta" in Mãe/Filha creates profiles; no manual forms or site APIs.
 */
'use strict';
(function () {
  const make = (tag, cls='', value) => {
    const el = document.createElement(tag);
    if (cls) el.className = cls;
    if (value !== undefined) el.textContent = String(value);
    return el;
  };
  const text = value => value == null || value === '' ? 'Não disponível' : String(value);
  function create({get}) {
    const root = make('div', 'accounts-workspace');
    const heading = make('div', 'accounts-heading');
    const titleBox = make('div');
    titleBox.append(
      make('div', 'workspace-eyebrow', 'PERFIS / LOCAL'),
      make('h1', '', 'Contas'),
      make('p', '', 'Perfis criados em Conta Mãe ou Conta Filha.')
    );
    const refreshButton = make('button', 'accounts-refresh', '↻ Atualizar');
    refreshButton.type = 'button';
    heading.append(titleBox, refreshButton);

    const message = make('p', 'accounts-message');
    message.hidden = true;
    message.setAttribute('role', 'status');

    const directory = make('section', 'accounts-directory');
    const toolbar = make('div', 'accounts-directory-tools');
    const search = make('input', 'accounts-search');
    search.type='search';
    search.placeholder='Buscar nome, usuário ou e-mail';
    search.setAttribute('aria-label', 'Buscar perfis');
    const filter = make('select', 'accounts-filter');
    filter.setAttribute('aria-label', 'Filtrar perfil');
    for (const [value,label] of [['','Todos'],['mae','Conta Mãe'],['filha','Conta Filha']]) {
      const item=make('option', '', label); item.value=value; filter.append(item);
    }
    toolbar.append(search,filter);
    const caption = make('p', 'accounts-count', 'Nenhum perfil salvo.');
    const cards=make('div','accounts-list');
    directory.append(toolbar,caption,cards);
    root.append(heading,message,directory);
    let profiles = [], busy=false, lastSnapshot=null, lastReadAt=0;
    function inform(msg,error=false) {
      message.hidden=!msg;
      message.textContent=msg||'';
      message.className='accounts-message '+(error?'is-error':'is-ok');
    }
    function cardFor(acc) {
      const data=acc.profileData||{};
      const card=make('article','accounts-card');
      card.dataset.accountId=acc.id;
      const head=make('div','accounts-card-heading');
      const headText=make('div');
      headText.append(make('h3','',text(acc.displayName)));
      headText.append(make('p','',
        (acc.profileScope==='mae'?'Conta Mãe':'Conta Filha')));
      head.append(headText);
      const summary=make('div','accounts-summary');
      for (const [label,value] of [
        ['Nome da conta',data.fullName],
        ['Username',data.username||acc.loginIdentifier],
        ['E-mail',data.email]
      ]) {
        const item=make('div','accounts-summary-item');
        item.append(make('span','',label),make('strong','',text(value)));
        summary.append(item);
      }
      card.append(head,summary);
      return card;
    }
    function render() {
      const q=search.value.trim().toLowerCase(),scope=filter.value;
      const filtered=profiles.filter(a=>
        (!scope||a.profileScope===scope) &&
        (!q||[a.displayName,a.loginIdentifier,a.profileData?.fullName,
          a.profileData?.username,a.profileData?.email]
          .join(' ').toLowerCase().includes(q)));
      caption.textContent=profiles.length+' perfil(is) salvo(s) · '+filtered.length+' exibido(s)';
      cards.replaceChildren();
      if (!filtered.length) {
        cards.append(make('p','accounts-empty',
          profiles.length ? 'Nenhum perfil corresponde à busca.' :
            'Nenhum perfil ainda. Entre em Conta Mãe ou Conta Filha e clique em Abrir Conta.'));
        return;
      }
      for(const account of filtered) cards.append(cardFor(account));
    }
    async function refresh() {
      if(busy) return;
      busy=true;refreshButton.disabled=true;lastReadAt=Date.now();inform('');
      try {
        const response=await get('/api/registration/accounts');
        const next=Array.isArray(response.accounts)?response.accounts:[];
        const snapshot=JSON.stringify(next);
        if(snapshot!==lastSnapshot) {
          profiles=next;
          lastSnapshot=snapshot;
          render();
        }
      } catch(err) {
        inform('Não foi possível consultar o JSON do agente Windows: '+(err.message||err),true);
      } finally {busy=false;refreshButton.disabled=false;}
    }
    search.addEventListener('input',render);
    filter.addEventListener('change',render);
    refreshButton.addEventListener('click',()=>{void refresh();});
    // The outer /api/state UI poll only re-renders on tree changes and does
    // NOT guarantee onShow() gets called regularly. Poll records ourselves.
    // Keep exactly one interval for the one singleton Contas view.
    setInterval(()=>{
      if(root.isConnected && !root.hidden) void refresh();
    },2000);
    return {node:root,onShow(){
      // Keep the account directory fresh when the workspace is opened.
      if(Date.now()-lastReadAt>2200) void refresh();
    }};
  }
  Object.defineProperty(window,'NexoraAccountsView',{
    value:Object.freeze({create}),configurable:false,writable:false,
  });
})();
