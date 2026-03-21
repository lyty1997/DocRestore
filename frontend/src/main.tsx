import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'

const rootEl = document.querySelector('#root')
if (!rootEl) {
  throw new Error('找不到 root 元素')
}

createRoot(rootEl).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
