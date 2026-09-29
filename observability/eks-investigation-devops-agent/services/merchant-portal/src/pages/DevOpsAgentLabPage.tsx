/**
 * The Lab route of the Helios portal: the shared Lab page (shared/lab/frontend) inside
 * this demo's router and auth. Everything it shows comes from lab/scenarios.yaml and the
 * Lab API; this file only wires navigation.
 */
import { useNavigate } from 'react-router-dom'
import LabPage from '../../../../../../shared/lab/frontend/LabPage'
import { useAuth } from '../context/AuthContext'

export default function DevOpsAgentLabPage() {
  const navigate = useNavigate()
  const { user, logout } = useAuth()
  return (
    <LabPage
      tagline="Break the Helios platform on purpose, watch the AWS DevOps Agent investigate, put it back."
      homeHref="/lab"
      onHome={() => navigate('/lab')}
      utilities={[
        { type: 'button', text: 'Back to Helios', iconName: 'arrow-left', onClick: () => navigate('/catalog') },
        {
          type: 'menu-dropdown',
          text: user?.signInDetails?.loginId ?? 'Account',
          iconName: 'user-profile',
          items: [{ id: 'signout', text: 'Sign out' }],
          onItemClick: async ({ detail }) => { if (detail.id === 'signout') { await logout(); navigate('/login') } },
        },
      ]}
    />
  )
}
