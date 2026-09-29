/**
 * The Lab route of the Helios portal. Everything it shows comes from lab/scenarios.yaml
 * and the Lab API (see ../lab); this file only sets the tagline.
 */
import LabPage from '../lab/LabPage'

export default function DevOpsAgentLabPage() {
  return <LabPage tagline="Break the Helios platform on purpose, watch the AWS DevOps Agent investigate, put it back." />
}
