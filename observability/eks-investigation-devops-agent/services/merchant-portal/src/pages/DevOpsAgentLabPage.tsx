/**
 * The Lab route of the Helios portal: the shared Lab page (shared/lab/frontend) inside
 * this demo's router and auth. Everything it shows comes from lab/scenarios.yaml and the
 * Lab API; this file only sets the tagline.
 */
import LabPage from '../../../../../../shared/lab/frontend/LabPage'

export default function DevOpsAgentLabPage() {
  return <LabPage tagline="Break the Helios platform on purpose, watch the AWS DevOps Agent investigate, put it back." />
}
