import { Outlet, Link, NavLink, useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import { useCart } from '../context/CartContext'
import './Layout.css'

export default function Layout() {
  const { user, logout } = useAuth()
  const { totalItems } = useCart()
  const navigate = useNavigate()

  async function handleLogout() {
    await logout()
    navigate('/login')
  }

  return (
    <div className="layout">
      <header className="header">
        <div className="container header-content">
          <Link to="/catalog" className="logo">
            <span className="logo-icon">◆</span>
            <span className="logo-text">Helios</span>
          </Link>
          <nav className="nav" aria-label="Main navigation">
            <NavLink to="/catalog" className={({ isActive }) => isActive ? 'nav-link active' : 'nav-link'}>
              Shop
            </NavLink>
            <NavLink to="/transactions" className={({ isActive }) => isActive ? 'nav-link active' : 'nav-link'}>
              Orders
            </NavLink>
          </nav>
          <div className="header-actions">
            <Link to="/cart" className="cart-button" aria-label={`Shopping cart with ${totalItems} items`}>
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="9" cy="21" r="1"/>
                <circle cx="20" cy="21" r="1"/>
                <path d="M1 1h4l2.68 13.39a2 2 0 0 0 2 1.61h9.72a2 2 0 0 0 2-1.61L23 6H6"/>
              </svg>
              {totalItems > 0 && <span className="cart-badge">{totalItems}</span>}
            </Link>
            <div className="user-menu">
              <span className="user-avatar">
                {user?.signInDetails?.loginId?.charAt(0).toUpperCase() || 'U'}
              </span>
              <span className="user-email">{user?.signInDetails?.loginId}</span>
              <button onClick={handleLogout} className="btn-logout">
                Sign Out
              </button>
            </div>
          </div>
        </div>
      </header>
      <main className="main container">
        <Outlet />
      </main>
      <footer className="footer">
        <div className="container footer-content">
          <div className="footer-brand">
            <span className="footer-logo">◆ Helios</span>
            <p className="footer-tagline">Secure payment processing for modern businesses.</p>
          </div>
          <div className="footer-links">
            <div className="footer-col">
              <h4>Shop</h4>
              <Link to="/catalog">All Products</Link>
              <Link to="/cart">Cart</Link>
              <Link to="/transactions">Order History</Link>
            </div>
            <div className="footer-col">
              <h4>Company</h4>
              <a href="#about">About Us</a>
              <a href="#careers">Careers</a>
              <a href="#contact">Contact</a>
            </div>
            <div className="footer-col">
              <h4>Support</h4>
              <a href="#help">Help Center</a>
              <a href="#shipping">Shipping Info</a>
              <a href="#returns">Returns</a>
            </div>
          </div>
          <div className="footer-bottom">
            <p>&copy; 2025 Helios Commerce. All rights reserved.</p>
            <div className="footer-legal">
              <a href="#privacy">Privacy Policy</a>
              <a href="#terms">Terms of Service</a>
            </div>
          </div>
        </div>
      </footer>
      <Link to="/lab" className="simulator-fab" title="DevOps Agent Lab — Demo Tool">
        🧪
      </Link>
    </div>
  )
}
