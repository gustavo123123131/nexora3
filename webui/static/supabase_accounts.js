/* Nexora cloud accounts: Supabase Auth + RLS, browser-only, public API key.
 * No service-role keys, personal registration values or browser cookies in the database.
 * Windows agent only receives sanitized site/profile metadata when running an action.
 */
'use strict';
(function () {
  const BASE = 'https://uulixxwsniibnpywxlov.supabase.co';
  // Supabase publishable key: safe for browser use WITH RLS enabled on each table.
  const KEY = 'sb_publishable_NIkCMznnIcfBZqvgeDFuVg_858DYjR7';
  const STORE = 'nexora-cloud-auth-v1';
  const LIMIT = 120;

  function create({postLocal, onAuthChange}) {
    let session = null, refreshPending = null;
    try { session = JSON.parse(sessionStorage.getItem(STORE) || 'null'); }
    catch (_) { session = null; }

    const authNode = document.createElement('section');
    authNode.className = 'accounts-panel accounts-cloud-auth';
    authNode.innerHTML = [
      '<header><div><h2>Banco Supabase · Nexora</h2>',
      '<p class="cloud-auth-status">Entre para acessar suas plataformas e contas na nuvem.</p></div></header>',
      '<form class="cloud-auth-form" autocomplete="on">',
      '<div class="accounts-two">',
      '<label>E-mail<input name="email" type="email" autocomplete="username" required></label>',
      '<label>Senha<input name="password" type="password" autocomplete="current-password" required minlength="6"></label></div>',
      '<div class="cloud-auth-buttons">',
      '<button type="submit" class="accounts-primary">Entrar no Supabase</button>',
      '<button type="button" class="cloud-create">Criar acesso</button>',
      '</div></form>',
      '<div class="cloud-auth-connected" hidden>',
      '<span class="cloud-auth-email"></span>',
      '<button type="button" class="cloud-logout">Sair da nuvem</button>',
      '</div><p class="cloud-auth-notice" role="status"></p>',
    ].join('');
    const form = authNode.querySelector('form');
    const message = authNode.querySelector('.cloud-auth-notice');
    const status = authNode.querySelector('.cloud-auth-status');
    const linked = authNode.querySelector('.cloud-auth-connected');
    const emailSpan = authNode.querySelector('.cloud-auth-email');

    function signedIn() { return !!(session && session.access_token); }
    function save(next) {
      session = next;
      if (next) sessionStorage.setItem(STORE, JSON.stringify(next));
      else sessionStorage.removeItem(STORE);
      render();
      onAuthChange(signedIn());
    }
    function render() {
      const ok = signedIn();
      form.hidden = ok;
      linked.hidden = !ok;
      status.textContent = ok ? 'Sua sessão está protegida pelas permissões do Supabase.' :
        'Entre ou crie seu acesso para gerenciar os registros na nuvem.';
      emailSpan.textContent = ok ? (session.user?.email || 'Conectado') : '';
    }

    async function transport(path, {method='GET', body, token, extraHeaders={}}={}) {
      const headers = {'apikey': KEY, ...extraHeaders};
      if (body !== undefined) headers['Content-Type'] = 'application/json';
      if (token) headers.Authorization = 'Bearer ' + token;
      const response = await fetch(BASE + path, {
        method, headers, mode: 'cors', cache: 'no-store',
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      const raw = await response.text();
      let data = null;
      try { data = raw ? JSON.parse(raw) : null; }
      catch (_) { data = raw; }
      if (!response.ok) {
        const reason = data && (data.msg || data.error_description || data.message || data.error || data.details);
        throw new Error('Supabase HTTP ' + response.status + (typeof reason === 'string' ? ': ' + reason : ''));
      }
      return data;
    }
    function storeAuth(data) {
      if (!data || !data.access_token || !data.refresh_token) return false;
      save({
        access_token: data.access_token, refresh_token: data.refresh_token,
        expires_at: Date.now() + Number(data.expires_in || 3600) * 1000,
        user: data.user || session?.user || {},
      });
      return true;
    }
    async function token() {
      if (!signedIn()) throw new Error('Entre no Supabase para acessar suas contas.');
      if (Number(session.expires_at) > Date.now() + 60000) return session.access_token;
      if (!refreshPending) {
        refreshPending = transport('/auth/v1/token?grant_type=refresh_token', {
          method: 'POST', body: {refresh_token: session.refresh_token},
        }).then(data => {
          if (!storeAuth(data)) throw new Error('Sessão não renovada.');
          return session.access_token;
        }).catch(error => {
          save(null);
          throw new Error('Sessão Supabase expirada. Entre novamente. ' + error.message);
        }).finally(() => { refreshPending = null; });
      }
      return refreshPending;
    }
    async function rest(path, options={}) {
      const jwt = await token();
      const extraHeaders = {...options.extraHeaders};
      if (options.method && options.method !== 'GET') {
        extraHeaders.Prefer = options.method === 'DELETE' ? 'return=minimal' : 'return=representation';
      }
      try { return await transport('/rest/v1/' + path, {...options,token:jwt,extraHeaders}); }
      catch (error) {
        if (error.message.includes('HTTP 401')) {
          session.expires_at = 0;
          const nextJwt = await token();
          return transport('/rest/v1/' + path, {...options,token:nextJwt,extraHeaders});
        }
        throw error;
      }
    }
    form.addEventListener('submit', event => {
      event.preventDefault();
      message.textContent = 'Conectando...';
      const email = form.elements.email.value.trim();
      const password = form.elements.password.value;
      void transport('/auth/v1/token?grant_type=password', {
        method:'POST', body:{email,password},
      }).then(data => {
        if (!storeAuth(data)) throw new Error('Não foi possível iniciar a sessão.');
        form.reset();
        message.textContent = 'Conectado ao Supabase.';
      }).catch(error => { message.textContent = error.message; });
    });
    authNode.querySelector('.cloud-create').addEventListener('click', () => {
      const email = form.elements.email.value.trim();
      const password = form.elements.password.value;
      if (!form.reportValidity()) return;
      message.textContent = 'Criando acesso...';
      void transport('/auth/v1/signup', {
        method:'POST',body:{email,password},
      }).then(data => {
        if (storeAuth(data)) message.textContent = 'Acesso criado e conectado.';
        else message.textContent = 'Acesso solicitado. Verifique seu e-mail para confirmar e depois entre.';
        form.elements.password.value = '';
      }).catch(error => { message.textContent = error.message; });
    });
    authNode.querySelector('.cloud-logout').addEventListener('click', () => {
      save(null);
      message.textContent = 'Sessão encerrada neste navegador.';
    });
    render();

    function siteFromRow(s) {
      return {
        id:s.site_key, displayName:s.display_name, baseUrl:s.base_url,
        registrationPath:s.registration_path, loginPath:s.login_path,
        fields:s.field_selectors || {}, submitSelector:s.submit_selector || '',
        successSelector:s.success_selector || '', authorized:s.authorized,
        _cloudId:s.id,
      };
    }
    function accountFromRow(a, knownSites) {
      const site=knownSites.find(x=>x._cloudId===a.site_id);
      return {
        id:a.id, siteId:site?.id || '', displayName:a.display_name, loginIdentifier:'',
        parentId:a.parent_id || null, profileScope:a.profile_scope,
        profileMode:a.profile_mode, profileIndex:a.profile_index,
        status:a.status, stage:a.stage, batchId:a.batch_id || null,
        createdAt:a.created_at, updatedAt:a.updated_at,
      };
    }
    async function sites() {
      const rows = await rest('nexora_sites?select=*&order=created_at.desc');
      return (rows || []).map(siteFromRow);
    }
    async function accounts(allSites) {
      const rows = await rest('nexora_accounts?select=*&order=created_at.desc&limit=1000');
      return (rows || []).map(a => accountFromRow(a,allSites));
    }
    async function get(path) {
      if (path === '/api/registration/sites') return {sites:await sites()};
      if (path === '/api/registration/accounts') {
        const s=await sites();
        return {accounts:await accounts(s)};
      }
      throw new Error('Consulta de contas desconhecida.');
    }
    async function mirrorAccount(accountId) {
      const s=await sites(), a=await accounts(s);
      const acc=a.find(x=>x.id===accountId);
      if (!acc) throw new Error('Conta não encontrada no Supabase.');
      const site=s.find(x=>x.id===acc.siteId);
      if (!site) throw new Error('Site não encontrado no Supabase.');
      const parent=acc.parentId ? a.find(x=>x.id===acc.parentId) : null;
      if (acc.parentId && !parent) throw new Error('Conta mãe ausente.');
      await postLocal('/api/registration/cloud/sync', {
        sites:[{
          id:site.id,displayName:site.displayName,baseUrl:site.baseUrl,
          registrationPath:site.registrationPath,loginPath:site.loginPath,
          fields:site.fields,submitSelector:site.submitSelector,
          successSelector:site.successSelector,authorized:site.authorized,
        }],
        accounts:[...(parent ? [parent] : []),acc],
      });
    }
    async function statusUpdate(account, action) {
      if (!account || !account.id) return;
      // Status is observed by the Windows Playwright backend, not HTTP 200 alone.
      const status = account.status;
      const stage = account.stage;
      await rest('nexora_accounts?id=eq.'+encodeURIComponent(account.id),{
        method:'PATCH',body:{status,stage,updated_at:new Date().toISOString()},
      });
      const state = status === 'completed' ? 'completed' : status === 'failed' ? 'failed'
        : action === 'prepare' ? 'form_ready'
        : action === 'submit' || action === 'refresh' ? 'verification_required' : 'opening';
      await rest('nexora_registration_jobs?account_id=eq.'+encodeURIComponent(account.id),{
        method:'PATCH',body:{state,stage,updated_at:new Date().toISOString(),
          ...(state==='completed' || state==='failed' ? {finished_at:new Date().toISOString()} : {})},
      });
      const jobs=await rest('nexora_registration_jobs?select=id&account_id=eq.'+encodeURIComponent(account.id));
      if (jobs && jobs[0]) {
        await rest('nexora_registration_events',{
          method:'POST', body:{job_id:jobs[0].id,event_type:state},
        });
      }
    }
    async function post(path, body) {
      if (path === '/api/registration/sites') {
        const rows=await rest('nexora_sites', {
          method:'POST',body:{
            site_key:body.id,display_name:body.displayName,base_url:body.baseUrl,
            registration_path:body.registrationPath,login_path:body.loginPath,
            field_selectors:body.fields || {},submit_selector:body.submitSelector || null,
            success_selector:body.successSelector || null,authorized:!!body.authorized,
          },
        });
        return {site:siteFromRow(rows[0])};
      }
      if (path === '/api/registration/accounts') {
        const s=await sites(), site=s.find(x=>x.id===body.siteId);
        if (!site) throw new Error('Selecione uma plataforma cadastrada.');
        const mode=body.profileMode,scope=body.profileScope;
        const amount=Math.min(LIMIT,Number(body.quantity || 1));
        if (!Number.isInteger(amount) || amount<1 || amount>100) {
          throw new Error('Quantidade de contas inválida (1–100).');
        }
        if (amount>1) {
          const batch=await rest('rpc/nexora_create_batch',{
            method:'POST',body:{
              p_site_id:site._cloudId,p_scope:scope,p_mode:mode,
              p_quantity:amount,p_parent_id:body.parentId || null,
            },
          });
          return {batchId:batch,quantity:amount,account:{displayName:amount+' contas'}};
        }
        const rows=await rest('nexora_accounts',{
          method:'POST',body:{
            site_id:site._cloudId,display_name:body.displayName,
            profile_scope:scope,profile_mode:mode,
            profile_index:body.profileIndex,
            parent_id:body.parentId || null,
          },
        });
        const acc=accountFromRow(rows[0],s);
        await rest('nexora_registration_jobs',{method:'POST',body:{account_id:acc.id}});
        return {account:acc,quantity:1};
      }
      if (path === '/api/registration/accounts/delete') {
        await rest('nexora_accounts?id=eq.'+encodeURIComponent(body.accountId),{method:'DELETE'});
        // Deleting a remote record does not delete Chromium profiles/cookies.
        try { await postLocal(path,body); }
        catch (_) { /* the local mirror may not yet exist */ }
        return {deleted:true};
      }
      if (['/api/registration/accounts/open','/api/registration/prepare',
           '/api/registration/submit','/api/registration/refresh'].includes(path)) {
        await mirrorAccount(body.accountId);
        const result=await postLocal(path,body);
        if (result.account && path !== '/api/registration/accounts/open') {
          await statusUpdate(result.account,path.split('/').pop());
        }
        return result;
      }
      throw new Error('Ação de contas desconhecida.');
    }
    return {authNode,get,post,signedIn};
  }
  Object.defineProperty(window,'NexoraCloudAccounts',{
    value:Object.freeze({create}),writable:false,configurable:false,
  });
})();
