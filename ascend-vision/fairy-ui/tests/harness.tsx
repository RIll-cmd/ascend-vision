import { useState } from 'react';
import { createRoot } from 'react-dom/client';
import { FairyCompanion } from '../src/components/fairy-companion';
import '../src/styles.css';
function Harness() {
  const [mounted, setMounted] = useState(true);
  return <><button style={{ position: 'fixed', top: 0, left: 0, zIndex: 100 }} onClick={() => setMounted(value => !value)}>Toggle component mount</button>{mounted && <FairyCompanion/>}</>;
}
createRoot(document.getElementById('root')!).render(<Harness/>);
