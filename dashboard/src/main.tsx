import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import './identity.css';
import { App } from './App';
import { applyAppearance, readAppearance } from './Appearance';

applyAppearance(readAppearance());

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
