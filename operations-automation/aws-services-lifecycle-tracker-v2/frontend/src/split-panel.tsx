// Lets a page put content in the AppLayout split panel (Cloudscape split view
// pattern): the page owns what is shown and what "close" means, App owns the
// panel itself.
import { createContext, useContext, ReactNode } from 'react';

export interface SplitPanelContent {
  header: string;
  content: ReactNode;
  onClose: () => void;
}

export const SplitPanelContext = createContext<(panel: SplitPanelContent | null) => void>(() => {});

export const useSplitPanel = () => useContext(SplitPanelContext);
