/* Nexora Contas: profiles created by Abrir Conta, with optional owner-confirmed registration.
 * Real contact details are passed transiently to the existing browser watcher.
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
  const stageNames = {
    'profile-created':'Perfil criado', 'review':'Formulário preenchido; aguardando revisão',
    'submit-queued':'Envio solicitado', 'submitting':'Enviando formulário',
    'awaiting-provider':'Aguardando a plataforma',
    'provider-response-unverified':'Resposta recebida; aguardando confirmação',
    'provider-http-error':'Erro HTTP do provedor', 'provider-confirmed':'Cadastro confirmado',
    'timeout-unverified':'Tempo limite: resultado desconhecido',
    'submit-result-unknown':'Envio incerto; confira no site',
    'submit-button-missing':'Botão de registro ausente',
    'test-data-blocked':'Dados fictícios: cadastro real bloqueado',
    'form-not-found':'Formulário não encontrado', 'origin-changed':'Domínio mudou',
    'review-real-data':'Formulário preenchido',
    'browser-closed-unverified':'Navegador fechado antes da confirmação',
  };
  const stageLabel = key => stageNames[key] || text(key);

  function create({get,post}) {
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

    function realRegistrationForm(acc) {
      // Generated names and example.test addresses are not owner's confirmed identity.
      // Real data remains only in this form until the user explicitly requests submission.
      const section=make('section','accounts-registration-actions');
      section.style.gridColumn='1/-1';
      const toggle=make('button','accounts-submit-button','Cadastrar com meus dados');
      toggle.type='button';
      const form=make('form','accounts-real-form');
      form.hidden=true;
      const explanation=make('p','accounts-action-hint',
        'Preencha seus dados reais. O username e a senha deste perfil serão reutilizados. '+
        'O envio usa a janela já aberta; não armazena estes dados no perfil.');
      explanation.style.gridColumn='1/-1';
      form.append(explanation);
      function field(label,type,autocomplete) {
        const wrap=make('label','accounts-real-field',label);
        const input=make('input','accounts-real-input');
        input.type=type;
        input.autocomplete=autocomplete;
        input.maxLength=160;
        wrap.append(input);
        form.append(wrap);
        return input;
      }
      const fullName=field('Nome completo do titular','text','name');
      fullName.required=true;
      fullName.minLength=3;
      const email=field('Seu e-mail (ou telefone abaixo)','email','email');
      const phone=field('Seu telefone (ou e-mail acima)','tel','tel');
      const consentLabel=make('label','accounts-consent-label');
      const consent=make('input');
      consent.type='checkbox';
      consent.required=true;
      consentLabel.append(consent,make('span','',
        'Confirmo que sou o titular dos dados informados e autorizo o envio '+
        'do cadastro para o endereço indicado neste perfil.'));
      form.append(consentLabel);
      const send=make('button','accounts-submit-button','Confirmar e clicar em Registro');
      send.type='submit';
      const status=make('p','accounts-action-hint','');
      status.setAttribute('role','status');
      status.style.gridColumn='1/-1';
      form.append(send,status);
      toggle.addEventListener('click',()=>{
        form.hidden=!form.hidden;
        toggle.setAttribute('aria-expanded',String(!form.hidden));
        if(!form.hidden) fullName.focus();
      });
      toggle.setAttribute('aria-expanded','false');
      form.addEventListener('submit',async event=>{
        event.preventDefault();
        const name=fullName.value.trim();
        const mail=email.value.trim();
        const tel=phone.value.trim();
        if(name.length<3 || (!mail && !tel) || !consent.checked) {
          status.textContent='Informe seu nome, ao menos um contato real e confirme a autorização.';
          return;
        }
        if(!acc.targetUrl) {
          status.textContent='Este perfil não tem URL de cadastro.';
          return;
        }
        send.disabled=true;
        status.textContent='Solicitando envio no navegador do perfil...';
        try {
          const answer=await post('/api/registration/accounts/submit-existing',{
            accountId:acc.id,
            userConfirmed:true,
            values:{fullName:name,email:mail,phone:tel}
          });
          status.textContent=answer.duplicate
            ? 'Já existe uma solicitação pendente para este perfil.'
            : 'Envio solicitado. Acompanhe o status neste perfil; a plataforma ainda precisa confirmar o cadastro.';
          if(!answer.duplicate) {
            fullName.value='';
            email.value='';
            phone.value='';
            consent.checked=false;
          }
        } catch(err) {
          status.textContent='Não foi possível solicitar o envio: '+(err.message||err);
        } finally {
          send.disabled=false;
        }
      });
      section.append(toggle,form);
      return section;
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
        ['CPF gerado (não verificado)',data.cpf],
        ['Etapa do cadastro',stageLabel(acc.stage||acc.status||'local')],
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
        'Dados gerados para testes. Um CPF com dígitos verificadores válidos pode coincidir com o de uma pessoa real; a titularidade não foi verificada. Não use estes dados para se passar por alguém.'));
      for(const [label,value,protectedField] of [
        ['Nome completo',data.fullName||null,false],
        ['Username',data.username||acc.loginIdentifier,false],
        ['Senha gerada',data.password,true],
        ['E-mail de teste',data.email,false],
        ['Telefone de teste',data.phone,false],
        ['CPF gerado (não verificado)',data.cpf,false],
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
      const events=Array.isArray(acc.registrationLog)?acc.registrationLog.slice(-15).reverse():[];
      const logPanel=make('section','accounts-registration-log');
      logPanel.append(make('h4','','Histórico do cadastro'));
      if (!events.length) logPanel.append(make('p','','Aguardando eventos de cadastro.'));
      for (const entry of events) {
        const row=make('div','accounts-log-row');
        const status=(entry.httpStatus? ' · HTTP '+entry.httpStatus:'');
        row.append(make('time','',date(entry.at)),
                   make('span','',stageLabel(entry.stage)+status));
        logPanel.append(row);
      }
      if(acc.targetUrl && acc.status!=='completed') details.append(realRegistrationForm(acc));
      details.append(logPanel);
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
