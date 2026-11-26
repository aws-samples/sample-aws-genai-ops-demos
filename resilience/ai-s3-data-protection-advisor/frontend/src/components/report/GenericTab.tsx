import KeyValuePairs from '@cloudscape-design/components/key-value-pairs';
import { genericEntries } from '../../lib/tabs';

export default function GenericTab({ tab }: { tab: object }) {
  return <KeyValuePairs columns={1} items={genericEntries(tab).map((e) => ({ label: e.key, value: e.text }))} />;
}
