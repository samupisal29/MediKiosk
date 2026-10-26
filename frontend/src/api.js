// In development, Vite proxies /api to FastAPI so localhost/127.0.0.1 and LAN
// browser URLs work without requiring matching CORS hostnames.
const BASE = import.meta.env.VITE_API_URL || '';
export async function api(path, options={}) {
  const headers = options.body instanceof FormData ? {} : {'Content-Type':'application/json'};
  let res;
  try {
    res=await fetch(`${BASE}/api${path}`,{credentials:'include',...options,headers:{...headers,...options.headers}});
  } catch {
    throw new Error('Cannot reach the backend. Start the API from the backend folder with: uvicorn main:app --reload');
  }
  const data=await res.json().catch(()=>({detail:'The server returned an unreadable response.'}));
  if(!res.ok) throw new Error(data.detail||`Request failed (${res.status})`);
  return data;
}
