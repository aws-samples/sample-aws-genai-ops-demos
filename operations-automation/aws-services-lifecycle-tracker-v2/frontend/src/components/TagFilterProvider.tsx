// Owns the active tag filter (#164): reads it from the URL (?tag=BU:LOB1, a
// link wins) or the browser memory, writes it to both, hands it to the data
// layer (api.setActiveTagFilter) and re-mounts the pages when it changes so
// they reload already scoped. Also exposes the indicator line data.
import { createContext, useContext, useEffect, useMemo, useState, ReactNode, useCallback } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { setActiveTagFilter } from '../api';
import { TagFilter, ScopeStats, filtersToParams, paramsToFilters, loadStoredFilters, storeFilters } from '../tag-filter';

interface TagFilterState {
  filters: TagFilter[];
  setFilters: (f: TagFilter[]) => void;
  stats: ScopeStats | null;
  // changes every time the filter changes: pages are keyed on it to reload
  version: number;
}

const Ctx = createContext<TagFilterState>({ filters: [], setFilters: () => {}, stats: null, version: 0 });
export const useTagFilter = () => useContext(Ctx);

const sameFilters = (a: TagFilter[], b: TagFilter[]) => JSON.stringify(filtersToParams(a)) === JSON.stringify(filtersToParams(b));

export default function TagFilterProvider({ children }: { children: ReactNode }) {
  const location = useLocation();
  const navigate = useNavigate();
  const [filters, setFiltersState] = useState<TagFilter[]>(() => {
    const fromUrl = paramsToFilters(new URLSearchParams(window.location.search).getAll('tag'));
    return fromUrl.length ? fromUrl : loadStoredFilters();
  });
  const [stats, setStats] = useState<ScopeStats | null>(null);
  const [version, setVersion] = useState(0);

  // data layer gets the filter before any page loads
  useMemo(() => setActiveTagFilter(filters, setStats), [filters]);

  const setFilters = useCallback((next: TagFilter[]) => {
    if (sameFilters(next, filters)) return;
    storeFilters(next);
    setFiltersState(next);
    setVersion((v) => v + 1);
  }, [filters]);

  // Keep the URL in sync on every navigation: pages build links without the
  // tag parameter, so it is re-added here (replace, no history entry).
  useEffect(() => {
    const p = new URLSearchParams(location.search);
    const inUrl = paramsToFilters(p.getAll('tag'));
    if (sameFilters(inUrl, filters)) return;
    p.delete('tag');
    for (const t of filtersToParams(filters)) p.append('tag', t);
    navigate({ pathname: location.pathname, search: p.toString() ? `?${p}` : '' }, { replace: true });
  }, [location.pathname, location.search, filters, navigate]);

  const value = useMemo(() => ({ filters, setFilters, stats, version }), [filters, setFilters, stats, version]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
