'use strict';
const FIELD_NAMES = new Set(['username','email','phone','password','confirmPassword','fullName','cpf']);
const SITE_ID_RE = /^[a-z0-9][a-z0-9_-]{0,63}$/i;

function normalizeUrl(raw, { allowHttpLocalhost = false } = {}) {
  if (typeof raw !== 'string' || !raw.trim()) throw new Error('URL não informada');
  const input = raw.trim();
  const withScheme = /^[a-z][a-z\d+.-]*:\/\//i.test(input) ? input : `https://${input}`;
  let parsed;
  try { parsed = new URL(withScheme); } catch { throw new Error('URL inválida'); }
  const local = ['localhost','127.0.0.1','[::1]'].includes(parsed.hostname);
  if (!(parsed.protocol === 'https:' || (allowHttpLocalhost && local && parsed.protocol === 'http:'))) {
    throw new Error('Somente HTTPS (ou HTTP local explicitamente permitido)');
  }
  if (parsed.username || parsed.password) throw new Error('URL com credenciais não permitida');
  if (parsed.hash) parsed.hash = '';
  return parsed;
}
function sameOriginUrl(base, path) {
  if (typeof path !== 'string' || !path.trim()) throw new Error('Caminho obrigatório');
  const url = new URL(path, base);
  if (url.origin !== base.origin) throw new Error('Redirecionamento para origem diferente não permitido');
  return url.toString();
}
function validateSiteConfig(config, options = {}) {
  if (!config || typeof config !== 'object' || !SITE_ID_RE.test(config.id || '')) throw new Error('ID de site inválido');
  const base = normalizeUrl(config.baseUrl, options);
  if (config.authorized !== true) throw new Error('Site não habilitado: autorização explícita obrigatória');
  if (!config.fields || typeof config.fields !== 'object' || Array.isArray(config.fields)) throw new Error('Campos do site ausentes');
  const fields = {};
  for (const [key, field] of Object.entries(config.fields)) {
    if (!FIELD_NAMES.has(key)) throw new Error('Campo não suportado: ' + key);
    if (!field || typeof field.selector !== 'string' || !field.selector.trim() || field.selector.length > 200) throw new Error('Seletor inválido: ' + key);
    fields[key] = { selector: field.selector, required: field.required === true };
  }
  if (!Object.keys(fields).length) throw new Error('Configure pelo menos um campo');
  if (typeof config.submitSelector !== 'string' || !config.submitSelector.trim()) throw new Error('Botão de envio não configurado');
  const officialApi = config.officialApi;
  if (officialApi !== undefined) {
    if (!officialApi || officialApi.authorized !== true || !officialApi.baseUrl || !Array.isArray(officialApi.operations)) {
      throw new Error('API oficial exige autorização e operações declaradas');
    }
    const apiBase = normalizeUrl(officialApi.baseUrl, options);
    for (const op of officialApi.operations) {
      if (!op || !/^[a-z][a-z0-9_-]*$/i.test(op.name || '') || !['GET','POST','PUT','PATCH'].includes(op.method) || !op.path) throw new Error('Operação da API inválida');
      sameOriginUrl(apiBase, op.path);
    }
  }
  return Object.freeze({
    id: config.id,
    displayName: config.displayName || config.id,
    baseUrl: base.origin,
    registrationUrl: sameOriginUrl(base, config.registrationPath || '/register'),
    loginUrl: sameOriginUrl(base, config.loginPath || '/login'),
    fields: Object.freeze(fields),
    submitSelector: config.submitSelector,
    loginFields: Object.freeze(Object.fromEntries(Object.entries(config.loginFields || {}).map(([key, field]) => {
      if (!['username','email','password'].includes(key) || !field || typeof field.selector !== 'string' || !field.selector.trim()) throw new Error('Campo de login inválido: '+key);
      return [key, { selector: field.selector }];
    }))),
    officialApi: officialApi ? Object.freeze({
      baseUrl: normalizeUrl(officialApi.baseUrl, options).origin,
      operations: officialApi.operations.map(({name,method,path})=>({name,method,path})),
    }) : null
  });
}
function createSiteRegistry(configs, options) {
  const map = new Map();
  for (const config of configs) {
    const validated = validateSiteConfig(config, options);
    if (map.has(validated.id)) throw new Error('Site duplicado: '+validated.id);
    map.set(validated.id, validated);  }
  return { get(id) {const site=map.get(id); if(!site)throw new Error('Site não configurado: '+id); return site;}, list(){return [...map.values()];} };
}
module.exports = { normalizeUrl, validateSiteConfig, createSiteRegistry, sameOriginUrl };
