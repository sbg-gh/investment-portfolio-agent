import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App, UNAUTHENTICATED_EVENT } from './App';
import { createApi } from './api/client';
import './styles.css';

// A 401 from anywhere except sign-in means the server-side session ended: return to sign in.
const api = createApi(async (input, init) => {
  const response = await fetch(input, init);
  if (response.status === 401 && input !== '/api/session') {
    window.dispatchEvent(new Event(UNAUTHENTICATED_EVENT));
  }
  return response;
});

const root = document.getElementById('root');
if (root) {
  createRoot(root).render(
    <StrictMode>
      <App api={api} />
    </StrictMode>,
  );
}
