document.addEventListener('DOMContentLoaded',()=>{
  const message=document.getElementById('formMessage');
  async function send(form,url){message.textContent='';const fd=new FormData(form),payload=Object.fromEntries(fd.entries());for(const box of form.querySelectorAll('input[type=checkbox]'))payload[box.name]=box.checked;try{const d=await lv360.api(url,{method:'POST',body:JSON.stringify(payload)});message.style.color='#2e7d5b';message.textContent=d.message||'تمت العملية بنجاح';if(d.redirect)location.href=d.redirect}catch(e){message.style.color='#a83f43';message.textContent=e.message}}
  document.getElementById('loginForm')?.addEventListener('submit',e=>{e.preventDefault();send(e.currentTarget,'/api/auth/login')});
  document.getElementById('registerForm')?.addEventListener('submit',e=>{e.preventDefault();send(e.currentTarget,'/api/auth/register')});
  document.getElementById('forgotForm')?.addEventListener('submit',e=>{e.preventDefault();send(e.currentTarget,'/api/auth/forgot-password')});
  document.getElementById('resetForm')?.addEventListener('submit',e=>{e.preventDefault();send(e.currentTarget,'/api/auth/reset-password')});
  document.getElementById('changePasswordForm')?.addEventListener('submit',e=>{e.preventDefault();send(e.currentTarget,'/api/auth/change-password')});
});
