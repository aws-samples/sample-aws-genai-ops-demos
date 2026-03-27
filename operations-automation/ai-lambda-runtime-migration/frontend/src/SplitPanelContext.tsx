import { createContext, useContext, useState, ReactNode } from 'react';

interface SplitPanelState {
  content: ReactNode | null;
  header: string;
  isOpen: boolean;
}

interface SplitPanelContextType {
  state: SplitPanelState;
  openPanel: (header: string, content: ReactNode) => void;
  closePanel: () => void;
}

const SplitPanelContext = createContext<SplitPanelContextType>({
  state: { content: null, header: '', isOpen: false },
  openPanel: () => {},
  closePanel: () => {},
});

export function SplitPanelProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<SplitPanelState>({ content: null, header: '', isOpen: false });

  const openPanel = (header: string, content: ReactNode) => {
    setState({ content, header, isOpen: true });
  };

  const closePanel = () => {
    setState((prev) => ({ ...prev, isOpen: false }));
  };

  return (
    <SplitPanelContext.Provider value={{ state, openPanel, closePanel }}>
      {children}
    </SplitPanelContext.Provider>
  );
}

export const useSplitPanel = () => useContext(SplitPanelContext);
