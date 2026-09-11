import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import FairyEyeDemo from '@/demo';
import './styles.css';

createRoot(document.getElementById('root')!).render(<StrictMode><FairyEyeDemo runtime={new URLSearchParams(location.search).get('runtime') === '1'}/></StrictMode>);
