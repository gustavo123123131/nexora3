'use strict';
function validCpf(v) {
  const s = String(v || '').replace(/\D/g,'');
  if (!/^\d{11}$/.test(s) || /^(\d)\1{10}$/.test(s)) return false;
  for (let n=9;n<11;n++) {
    let sum=0;
    for (let i=0;i<n;i++) sum += Number(s[i])*(n+1-i);
    const d=((sum*10)%11)%10;
    if(d!==Number(s[n]))return false;
  }
  return true;
}
function validateFields(values, definitions) {
  if (!values || typeof values !== 'object' || Array.isArray(values)) return { ok:false, errors:{_:'Dados inválidos'} };
  const errors={}, clean={};
  for(const [key, def] of Object.entries(definitions)) {
    const value=values[key];
    if(value===undefined || value===null || String(value).trim()==='') {
      if(def.required) errors[key]='Campo obrigatório';
      continue;
    }
    if(typeof value!=='string' || value.length>256) { errors[key]='Valor inválido';continue; }
    const v=value.trim();
    if(key==='email' && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v)) errors[key]='E-mail inválido';
    if(key==='phone' && !/^\+?[\d\s()-]{10,20}$/.test(v)) errors[key]='Telefone inválido';
    if(key==='username' && !/^[\p{L}\p{N}._-]{3,40}$/u.test(v)) errors[key]='Usuário inválido';
    if(key==='password' && (value.length<12 || value.length>128)) errors[key]='Senha deve ter entre 12 e 128 caracteres';
    if(key==='fullName' && v.length<3) errors[key]='Nome inválido';
    if(key==='cpf' && !validCpf(v)) errors[key]='CPF inválido';
    clean[key] = key==='password' || key==='confirmPassword' ? value : v;
  }
  if('confirmPassword' in definitions && clean.confirmPassword !== undefined && clean.password !== undefined && clean.confirmPassword !== clean.password)
    errors.confirmPassword='As senhas não coincidem';
  return {ok: Object.keys(errors).length===0, errors, values:clean};
}
function generatePassword(length=20) {
  if(!Number.isInteger(length) || length<12 || length>128) throw new Error('Comprimento de senha inválido');
  const {randomInt} = require('node:crypto');
  const groups=['abcdefghjkmnpqrstuvwxyz','ABCDEFGHJKMNPQRSTUVWXYZ','23456789','!@#$%*-_'];
  const chars=groups.map(group => group[randomInt(group.length)]);
  const all=groups.join('');
  while(chars.length<length) chars.push(all[randomInt(all.length)]);
  for(let i=chars.length-1;i>0;i--) {const j=randomInt(i+1);[chars[i],chars[j]]=[chars[j],chars[i]];}
  return chars.join('');
}
module.exports={validateFields,validCpf,generatePassword};
