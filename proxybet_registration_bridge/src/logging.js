'use strict';
// Não repassa objetos arbitrários (podem conter senha, cookies, CPF ou token).
const ALLOWED_KEYS = new Set(['event','accountId','siteId','profileId','status','stage','code','fieldCount']);
function safeMeta(meta={}) {
  const out={};
  for (const [key,value] of Object.entries(meta||{})) {
    if(ALLOWED_KEYS.has(key) && (typeof value==='string'||typeof value==='number'||typeof value==='boolean'))
      out[key]=String(value).slice(0,100);
  }
  return out;
}
function createLogger(sink=()=>{}) {
  return (event,meta={}) => sink({time:new Date().toISOString(), event:String(event).slice(0,50), ...safeMeta(meta)});
}
module.exports={safeMeta,createLogger};
