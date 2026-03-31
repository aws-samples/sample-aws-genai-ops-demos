import { useState } from 'react'
import { useCart } from '../context/CartContext'
import { Product } from '../types'
import './CatalogPage.css'

const DEMO_PRODUCTS: Product[] = [
  {
    id: '1',
    name: 'Wireless Noise-Cancelling Headphones',
    description: 'Premium over-ear headphones with active noise cancellation, 30-hour battery life, and Hi-Res audio support.',
    price: 249.99,
    currency: 'EUR',
    imageUrl: '/images/headphones.svg',
    category: 'Electronics',
  },
  {
    id: '2',
    name: 'Mechanical Keyboard RGB',
    description: 'Hot-swappable mechanical keyboard with per-key RGB lighting, PBT keycaps, and USB-C connectivity.',
    price: 129.00,
    currency: 'EUR',
    imageUrl: '/images/keyboard.svg',
    category: 'Electronics',
  },
  {
    id: '3',
    name: 'Ultrawide Monitor 34"',
    description: '34-inch UWQHD curved monitor, 144Hz refresh rate, 1ms response time. Perfect for productivity and gaming.',
    price: 599.00,
    currency: 'EUR',
    imageUrl: '/images/monitor.svg',
    category: 'Electronics',
  },
  {
    id: '4',
    name: 'Ergonomic Office Chair',
    description: 'Adjustable lumbar support, breathable mesh back, 4D armrests, and certified for 8+ hours of comfort.',
    price: 449.00,
    currency: 'EUR',
    imageUrl: '/images/chair.svg',
    category: 'Furniture',
  },
  {
    id: '5',
    name: 'Standing Desk Electric',
    description: 'Electric height-adjustable desk with memory presets, cable management tray, and bamboo desktop.',
    price: 699.00,
    currency: 'EUR',
    imageUrl: '/images/desk.svg',
    category: 'Furniture',
  },
  {
    id: '6',
    name: 'USB-C Docking Station',
    description: 'Triple display docking station with 100W power delivery, 10Gbps data transfer, and Ethernet port.',
    price: 179.99,
    currency: 'EUR',
    imageUrl: '/images/dock.svg',
    category: 'Accessories',
  },
  {
    id: '7',
    name: 'Webcam 4K Pro',
    description: 'Ultra HD webcam with auto-framing, built-in ring light, dual noise-cancelling microphones.',
    price: 149.00,
    currency: 'EUR',
    imageUrl: '/images/webcam.svg',
    category: 'Electronics',
  },
  {
    id: '8',
    name: 'Laptop Backpack',
    description: 'Water-resistant backpack fits up to 16" laptops. Anti-theft pocket, USB charging port, and organizer compartments.',
    price: 79.99,
    currency: 'EUR',
    imageUrl: '/images/backpack.svg',
    category: 'Accessories',
  },
  {
    id: '9',
    name: 'Wireless Charging Pad',
    description: 'Qi-certified 15W fast wireless charger with LED indicator and foreign object detection.',
    price: 34.99,
    currency: 'EUR',
    imageUrl: '/images/charger.svg',
    category: 'Accessories',
  },
]

const CATEGORIES = ['All', ...Array.from(new Set(DEMO_PRODUCTS.map(p => p.category)))]

const PRODUCT_ICONS: Record<string, string> = {
  'Wireless Noise-Cancelling Headphones': '🎧',
  'Mechanical Keyboard RGB': '⌨️',
  'Ultrawide Monitor 34"': '🖥️',
  'Ergonomic Office Chair': '🪑',
  'Standing Desk Electric': '🪵',
  'USB-C Docking Station': '🔌',
  'Webcam 4K Pro': '📷',
  'Laptop Backpack': '🎒',
  'Wireless Charging Pad': '🔋',
}

const PRODUCT_GRADIENTS: Record<string, string> = {
  'Electronics': 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
  'Furniture': 'linear-gradient(135deg, #f093fb 0%, #f5576c 100%)',
  'Accessories': 'linear-gradient(135deg, #4facfe 0%, #00f2fe 100%)',
}

export default function CatalogPage() {
  const { addToCart } = useCart()
  const [selectedCategory, setSelectedCategory] = useState('All')
  const [addedId, setAddedId] = useState<string | null>(null)

  const filteredProducts = selectedCategory === 'All'
    ? DEMO_PRODUCTS
    : DEMO_PRODUCTS.filter(p => p.category === selectedCategory)

  function formatPrice(price: number, currency: string) {
    return new Intl.NumberFormat('de-DE', {
      style: 'currency',
      currency,
    }).format(price)
  }

  function handleAddToCart(product: Product) {
    addToCart(product)
    setAddedId(product.id)
    setTimeout(() => setAddedId(null), 1200)
  }

  return (
    <div className="catalog-page">
      <section className="hero-banner">
        <div className="hero-content">
          <span className="hero-badge">New Arrivals</span>
          <h1>Premium Tech &amp; Office Essentials</h1>
          <p className="hero-subtitle">
            Discover our curated collection of high-quality products for your workspace. Free shipping on orders over €200.
          </p>
        </div>
        <div className="hero-stats">
          <div className="stat-item">
            <span className="stat-number">2,400+</span>
            <span className="stat-label">Happy Customers</span>
          </div>
          <div className="stat-item">
            <span className="stat-number">99.8%</span>
            <span className="stat-label">Uptime SLA</span>
          </div>
          <div className="stat-item">
            <span className="stat-number">24/7</span>
            <span className="stat-label">Support</span>
          </div>
        </div>
      </section>

      <section className="catalog-controls">
        <div className="category-filters">
          {CATEGORIES.map(cat => (
            <button
              key={cat}
              className={`category-pill ${selectedCategory === cat ? 'active' : ''}`}
              onClick={() => setSelectedCategory(cat)}
            >
              {cat}
            </button>
          ))}
        </div>
        <p className="results-count">{filteredProducts.length} products</p>
      </section>

      <div className="product-grid">
        {filteredProducts.map((product) => (
          <div key={product.id} className="product-card card">
            <div
              className="product-image"
              style={{ background: PRODUCT_GRADIENTS[product.category] || PRODUCT_GRADIENTS['Electronics'] }}
            >
              <span className="product-icon">{PRODUCT_ICONS[product.name] || '📦'}</span>
            </div>
            <div className="product-info">
              <span className="product-category">{product.category}</span>
              <h3 className="product-name">{product.name}</h3>
              <p className="product-description">{product.description}</p>
              <div className="product-rating">
                <span className="stars">★★★★★</span>
                <span className="rating-count">(128)</span>
              </div>
              <div className="product-footer">
                <span className="product-price">
                  {formatPrice(product.price, product.currency)}
                </span>
                <button
                  className={`btn-add-cart ${addedId === product.id ? 'added' : ''}`}
                  onClick={() => handleAddToCart(product)}
                >
                  {addedId === product.id ? '✓ Added' : 'Add to Cart'}
                </button>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
