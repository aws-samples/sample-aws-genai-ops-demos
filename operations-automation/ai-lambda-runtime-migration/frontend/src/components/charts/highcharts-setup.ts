// Single Highcharts instance for the whole app.
//
// Highcharts v12 (installed: 12.6.2) ships its optional modules as ESM that
// self-register — but only against the Highcharts instance they import. The
// package's bare `highcharts` specifier resolves to the UMD build, while
// `highcharts/esm/highcharts-more.js` imports `highcharts/esm/highcharts.js`;
// those are two different module instances. Mixing bare `import 'highcharts/
// highcharts-more'` with `import Highcharts from 'highcharts'` therefore
// registers the 'more' series types (bubble) onto an instance the charts never
// use, leaving SeriesRegistry undefined and crashing at runtime.
//
// To guarantee one instance, every chart imports Highcharts from HERE, from the
// ESM core, and this module imports the ESM `highcharts-more` (bubble) and
// accessibility modules so they self-register onto that same instance before any
// chart is created. No manual HighchartsMore(Highcharts) init (the pre-v12
// pattern, itself a documented cause of the same crash).
import Highcharts from 'highcharts/esm/highcharts.js';
import 'highcharts/esm/highcharts-more.js';
import 'highcharts/esm/modules/accessibility.js';

export default Highcharts;
