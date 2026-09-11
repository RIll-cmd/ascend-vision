"use client";

import { FairyCompanion } from '@/components/fairy-companion';

/** Camera ownership, face tracking, expression controls, and audio-reactivity demo. */
export default function FairyEyeDemo({ runtime = false }: { runtime?: boolean }) {
  return <FairyCompanion runtime={runtime}/>;
}
