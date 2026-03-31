import { useState, FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { useCart } from '../context/CartContext'
import { useAuth } from '../context/AuthContext'
import { paymentApi } from '../services/api'
import { PaymentResponse } from '../types'
import './CheckoutPage.css'

interface CardDetails {
  cardNumber: string
  expiryDate: string
  cvv: string
  cardholderName: string
}

export default function CheckoutPage() {
  const { items, totalAmount, clearCart } = useCart()
  const { getAccessToken } = useAuth()
  const navigate = useNavigate()

  const [cardDetails, setCardDetails] = useState<CardDetails>({
    cardNumber: '4242 4242 4242 4242',
    expiryDate: '12/28',
    cvv: '123',
    cardholderName: 'Demo Merchant',
  })
  const [isProcessing, setIsProcessing] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState<PaymentResponse | null>(null)

  const totalWithTax = totalAmount * 1.19

  function formatPrice(price: number) {
    return new Intl.NumberFormat('de-DE', {
      style: 'currency',
      currency: 'EUR',
    }).format(price)
  }

  function formatCardNumber(value: string) {
    const digits = value.replace(/\D/g, '')
    const groups = digits.match(/.{1,4}/g)
    return groups ? groups.join(' ').substring(0, 19) : ''
  }

  function formatExpiryDate(value: string) {
    const digits = value.replace(/\D/g, '')
    if (digits.length >= 2) {
      return `${digits.substring(0, 2)}/${digits.substring(2, 4)}`
    }
    return digits
  }

  function tokenizeCard(card: CardDetails): string {
    // Mock tokenization - in production, this would use a secure tokenization service
    const hash = btoa(`${card.cardNumber}:${card.expiryDate}:${Date.now()}`)
    return `tok_${hash.substring(0, 24)}`
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError('')
    setIsProcessing(true)

    try {
      const token = await getAccessToken()
      if (!token) {
        throw new Error('Session expired. Please log in again.')
      }

      // Tokenize card details (mock)
      const paymentMethodToken = tokenizeCard(cardDetails)

      // Submit payment
      const response = await paymentApi.authorize(
        {
          amount: Math.round(totalWithTax * 100) / 100,
          currency: 'EUR',
          paymentMethodToken,
          metadata: {
            items: items.map(i => ({ id: i.product.id, qty: i.quantity })),
          },
        },
        token
      )

      setResult(response)

      if (response.status === 'AUTHORIZED') {
        // Auto-capture for demo
        await paymentApi.capture(response.id, token)
        clearCart()
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Payment failed')
    } finally {
      setIsProcessing(false)
    }
  }

  if (items.length === 0 && !result) {
    navigate('/cart')
    return null
  }

  if (result) {
    return (
      <div className="checkout-page">
        <div className="payment-result card">
          {result.status === 'AUTHORIZED' || result.status === 'CAPTURED' ? (
            <>
              <div className="result-icon success">✓</div>
              <h2>Payment Successful!</h2>
              <p>Your transaction has been processed.</p>
              <div className="result-details">
                <p><strong>Transaction ID:</strong> {result.id}</p>
                <p><strong>Status:</strong> {result.status}</p>
                {result.correlationId && (
                  <p><strong>Correlation ID:</strong> {result.correlationId}</p>
                )}
              </div>
              <button 
                className="btn-primary"
                onClick={() => navigate('/transactions')}
              >
                View Transactions
              </button>
            </>
          ) : (
            <>
              <div className="result-icon error">✗</div>
              <h2>Payment Failed</h2>
              <p>{result.errorMessage || 'An error occurred during payment processing.'}</p>
              {result.errorCode && <p className="error-code">Error: {result.errorCode}</p>}
              <button 
                className="btn-primary"
                onClick={() => setResult(null)}
              >
                Try Again
              </button>
            </>
          )}
        </div>
      </div>
    )
  }

  return (
    <div className="checkout-page">
      <h1>Checkout</h1>
      
      <div className="checkout-content">
        <form onSubmit={handleSubmit} className="payment-form card">
          <h2>Payment Details</h2>
          
          <div className="form-group">
            <label htmlFor="cardholderName">Cardholder Name</label>
            <input
              id="cardholderName"
              type="text"
              value={cardDetails.cardholderName}
              onChange={(e) => setCardDetails(prev => ({ 
                ...prev, 
                cardholderName: e.target.value 
              }))}
              placeholder="John Doe"
              required
              autoComplete="cc-name"
            />
          </div>

          <div className="form-group">
            <label htmlFor="cardNumber">Card Number</label>
            <input
              id="cardNumber"
              type="text"
              value={cardDetails.cardNumber}
              onChange={(e) => setCardDetails(prev => ({ 
                ...prev, 
                cardNumber: formatCardNumber(e.target.value) 
              }))}
              placeholder="4242 4242 4242 4242"
              required
              maxLength={19}
              autoComplete="cc-number"
            />
          </div>

          <div className="form-row">
            <div className="form-group">
              <label htmlFor="expiryDate">Expiry Date</label>
              <input
                id="expiryDate"
                type="text"
                value={cardDetails.expiryDate}
                onChange={(e) => setCardDetails(prev => ({ 
                  ...prev, 
                  expiryDate: formatExpiryDate(e.target.value) 
                }))}
                placeholder="MM/YY"
                required
                maxLength={5}
                autoComplete="cc-exp"
              />
            </div>

            <div className="form-group">
              <label htmlFor="cvv">CVV</label>
              <input
                id="cvv"
                type="text"
                value={cardDetails.cvv}
                onChange={(e) => setCardDetails(prev => ({ 
                  ...prev, 
                  cvv: e.target.value.replace(/\D/g, '').substring(0, 4) 
                }))}
                placeholder="123"
                required
                maxLength={4}
                autoComplete="cc-csc"
              />
            </div>
          </div>

          {error && <p className="error-message">{error}</p>}

          <button 
            type="submit" 
            className="btn-primary pay-button"
            disabled={isProcessing}
          >
            {isProcessing ? 'Processing...' : `Pay ${formatPrice(totalWithTax)}`}
          </button>

          <p className="security-note">
            🔒 Your payment is secured with TLS encryption
          </p>
        </form>

        <div className="order-summary card">
          <h2>Order Summary</h2>
          <div className="summary-items">
            {items.map((item) => (
              <div key={item.product.id} className="summary-item">
                <span>{item.product.name} × {item.quantity}</span>
                <span>{formatPrice(item.product.price * item.quantity)}</span>
              </div>
            ))}
          </div>
          <div className="summary-totals">
            <div className="summary-row">
              <span>Subtotal</span>
              <span>{formatPrice(totalAmount)}</span>
            </div>
            <div className="summary-row">
              <span>Tax (19%)</span>
              <span>{formatPrice(totalAmount * 0.19)}</span>
            </div>
            <div className="summary-row total">
              <span>Total</span>
              <span>{formatPrice(totalWithTax)}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
