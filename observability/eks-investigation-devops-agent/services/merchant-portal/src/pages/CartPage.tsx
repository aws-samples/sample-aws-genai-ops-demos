import { Link } from 'react-router-dom'
import { useCart } from '../context/CartContext'
import './CartPage.css'

export default function CartPage() {
  const { items, removeFromCart, updateQuantity, totalAmount, clearCart } = useCart()

  function formatPrice(price: number, currency: string = 'EUR') {
    return new Intl.NumberFormat('de-DE', {
      style: 'currency',
      currency,
    }).format(price)
  }

  if (items.length === 0) {
    return (
      <div className="cart-page">
        <h1>Shopping Cart</h1>
        <div className="empty-cart card">
          <div className="empty-cart-icon">🛒</div>
          <p>Your cart is empty</p>
          <Link to="/catalog" className="btn-primary">
            Continue Shopping
          </Link>
        </div>
      </div>
    )
  }

  return (
    <div className="cart-page">
      <h1>Shopping Cart</h1>
      <p>{items.length} {items.length === 1 ? 'item' : 'items'} in your cart</p>

      <div className="cart-content">
        <div className="cart-items">
          {items.map((item) => (
            <div key={item.product.id} className="cart-item card">
              <div className="cart-item-info">
                <h3>{item.product.name}</h3>
                <p className="cart-item-price">
                  {formatPrice(item.product.price, item.product.currency)} each
                </p>
              </div>

              <div className="cart-item-actions">
                <div className="quantity-control">
                  <button
                    onClick={() => updateQuantity(item.product.id, item.quantity - 1)}
                    aria-label="Decrease quantity"
                  >
                    −
                  </button>
                  <span>{item.quantity}</span>
                  <button
                    onClick={() => updateQuantity(item.product.id, item.quantity + 1)}
                    aria-label="Increase quantity"
                  >
                    +
                  </button>
                </div>

                <span className="cart-item-subtotal">
                  {formatPrice(item.product.price * item.quantity, item.product.currency)}
                </span>

                <button
                  className="btn-remove"
                  onClick={() => removeFromCart(item.product.id)}
                >
                  Remove
                </button>
              </div>
            </div>
          ))}
        </div>

        <div className="cart-summary card">
          <h2>Order Summary</h2>
          <div className="summary-row">
            <span>Subtotal</span>
            <span>{formatPrice(totalAmount)}</span>
          </div>
          <div className="summary-row">
            <span>Shipping</span>
            <span>{totalAmount >= 200 ? 'Free' : formatPrice(9.99)}</span>
          </div>
          <div className="summary-row">
            <span>Tax (19%)</span>
            <span>{formatPrice(totalAmount * 0.19)}</span>
          </div>
          <div className="summary-row total">
            <span>Total</span>
            <span>{formatPrice(totalAmount * 1.19 + (totalAmount >= 200 ? 0 : 9.99))}</span>
          </div>

          <Link to="/checkout" className="btn-primary checkout-button">
            Proceed to Checkout
          </Link>

          <button className="btn-clear" onClick={clearCart}>
            Clear Cart
          </button>
        </div>
      </div>
    </div>
  )
}
