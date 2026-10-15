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
  const date = value => {
    if (!value) return 'Não disponível';
    const d = new Date(value);
    return Number.isNaN(d.getTime()) ? String(value) : d.toLocaleString('pt-BR');
  };

  function create({get}) {
    const root = make('div', 'accounts-workspace');
    const heading = make('div', 'accounts-heading');
    const titleBox = make('div');
    titleBox.append(
      make('div', 'workspace-eyebrow', 'PERFIS / LOCAL'),
      make('h1', '', 'Contas'),
      make('p', '', 'Perfis criados em Conta Mãe ou Conta Filha. Dados fictícios de teste, não verificados.')
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
    function detailValue(title, value, protect=false) {
      const wrapper=make('div','accounts-detail-field');
      wrapper.append(make('span','accounts-detail-label',title));
      if (!protect || !value) {
        wrapper.append(make('strong','accounts-detail-value',text(value)));
      } else {
        const panel=make('div','accounts-private-value');
        const input=make('input','accounts-protected');
        input.type='password'; input.readOnly=true; input.value=String(value);
        input.setAttribute('aria-label',title);
        const toggle=make('button','accounts-reveal','Mostrar');
        toggle.type='button';
        toggle.addEventListener('click',()=>{
          const show=input.type==='password';
          input.type=show?'text':'password';
          toggle.textContent=show?'Ocultar':'Mostrar';
        });
        panel.append(input,toggle);wrapper.append(panel);
      }
      return wrapper;
    }
    function cardFor(acc) {
      const data=acc.profileData||{};
      const card=make('article','accounts-card');
      card.dataset.accountId=acc.id;
      const head=make('div','accounts-card-heading');
      const headText=make('div');
      headText.append(make('h3','',text(acc.displayName)));
      const mode=acc.profileMode||'desktop';
      headText.append(make('p','',((acc.profileScope==='mae')?'Mãe':'Filha')+
        ' · '+mode+' · perfil '+text(acc.profileIndex)));
      head.append(headText,make('span','accounts-test-badge',
        data.isTestData ? 'Dados fictícios' : 'Perfil local'));
      const summary=make('div','accounts-summary');
      for(const [label,value] of [
        ['Username',data.username||acc.loginIdentifier],
        ['E-mail',data.email],
        ['Telefone',data.phone],
        ['CPF',data.cpf ? 'Protegido · ver detalhes' : 'Não disponível'],
        ['Criado em',date(acc.createdAt)]
      ]) {
        const item=make('div','accounts-summary-item');
        item.append(make('span','',label),make('strong','',text(value)));
        summary.append(item);
      }
      const button=make('button','accounts-details-button','Ver detalhes');
      button.type='button';button.setAttribute('aria-expanded','false');
      const details=make('div','accounts-details');details.hidden=true;
      details.append(make('p','accounts-detail-warning',
        'Identidade fictícia de teste. CPF com dígitos verificadores válidos, sem comprovação de titularidade. Não utilize estes dados para se passar por uma pessoa real.'));
      for(const [label,value,protectedField] of [
        ['Nome completo',data.fullName||null,false],
        ['Username',data.username||acc.loginIdentifier,false],
        ['Senha gerada',data.password,true],
        ['E-mail de teste',data.email,false],
        ['Telefone de teste',data.phone,false],
        ['CPF fictício não verificado',data.cpf,true],
        ['Tipo / modo',(acc.profileScope==='mae'?'Mãe':'Filha')+' · '+mode,false],
        ['Número do perfil',acc.profileIndex,false],
        ['Criado em',date(acc.createdAt),false]
      ]) details.append(detailValue(label,value,protectedField));
      button.addEventListener('click',()=>{
        details.hidden=!details.hidden;
        const expanded=!details.hidden;
        button.setAttribute('aria-expanded',String(expanded));
        button.textContent=expanded?'Fechar detalhes':'Ver detalhes';
        if (!expanded) for(const field of details.querySelectorAll('.accounts-protected')) {
          field.type='password';field.nextElementSibling.textContent='Mostrar';
        }
      });
      card.append(head,summary,button,details);
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
    return {node:root,onShow(){
      // The outer workspace polls frequently; don't collapse open details on polls.
      if(Date.now()-lastReadAt>2200) void refresh();
    }};
  }
  Object.defineProperty(window,'NexoraAccountsView',{
    value:Object.freeze({create}),configurable:false,writable:false,
  });
})();
