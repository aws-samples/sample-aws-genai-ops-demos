import { useState, useEffect } from 'react'
import { useParams, Link } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import { paymentApi } from '../services/api'
import { Transaction, TransactionStatus } from '../types'
import './TransactionDetailPage.css'

export default function TransactionDetailPage() {
  const { id } = useParams<{ id: string }>()
  const { getAccessToken } = useAuth()
  const [transaction, setTransaction] = useState<Transaction | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState('')
  const [actionLoading, setActionLoading] = useState(false)

  useEffect(() => {
    if (id) {
      loadTransaction(id)
    }
  }, [id])

  async function loadTransaction(transactionId: string) {
    setIsLoading(true)
    setError('')

    try {
      const token = await getAccessToken()
      if (!token) {
        throw new Error('Session expired')
      }

      const data = await paymentApi.getTransaction(transactionId, token)
      setTransaction(data)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load transaction')
    } finally {
      setIsLoading(false)
    }
  }

  async function handleCapture() {
    if (!transaction) return
    setActionLoading(true)

    try {
      const token = await getAccessToken()
      if (!token) throw new Error('Session expired')

      await paymentApi.capture(transaction.id, token)
      await loadTransaction(transaction.id)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Capture failed')
    } finally {
      setActionLoading(false)
    }
  }

  async function handleRefund() {
    if (!transaction) return
    setActionLoading(true)

    try {
      const token = await getAccessToken()
      if (!token) throw new Error('Session expired')

      await paymentApi.refund(transaction.id, token)
      await loadTransaction(transaction.id)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Refund failed')
    } finally {
      setActionLoading(false)
    }
  }

  function formatPrice(amount: number, currency: string) {
    return new Intl.NumberFormat('de-DE', {
      style: 'currency',
      currency,
    }).format(amount)
  }

  function formatDate(dateString: string) {
    return new Date(dateString).toLocaleString('de-DE', {
      dateStyle: 'full',
      timeStyle: 'medium',
    })
  }

  function getStatusClass(status: TransactionStatus) {
    switch (status) {
      case 'CAPTURED':
        return 'status-success'
      case 'AUTHORIZED':
        return 'status-pending'
      case 'REFUNDED':
        return 'status-refunded'
      case 'FAILED':
      case 'CANCELLED':
        return 'status-failed'
      default:
        return 'status-default'
    }
  }

  if (isLoading) {
    return (
      <div className="transaction-detail-page">
        <div className="loading">Loading transaction...</div>
      </div>
    )
  }

  if (error || !transaction) {
    return (
      <div className="transaction-detail-page">
        <div className="error-state card">
          <p>{error || 'Transaction not found'}</p>
          <Link to="/transactions" className="btn-primary">
            Back to Transactions
          </Link>
        </div>
      </div>
    )
  }

  return (
    <div className="transaction-detail-page">
      <div className="page-header">
        <Link to="/transactions" className="back-link">
          ← Back to Transactions
        </Link>
        <h1>Transaction Details</h1>
      </div>

      <div className="detail-content">
        <div className="detail-card card">
          <div className="detail-header">
            <div>
              <span className={`status-badge ${getStatusClass(transaction.status)}`}>
                {transaction.status}
              </span>
              <h2 className="amount">
                {formatPrice(transaction.amount, transaction.currency)}
              </h2>
            </div>
            <div className="actions">
              {transaction.status === 'AUTHORIZED' && (
                <button 
                  className="btn-primary"
                  onClick={handleCapture}
                  disabled={actionLoading}
                >
                  {actionLoading ? 'Processing...' : 'Capture Payment'}
                </button>
              )}
              {transaction.status === 'CAPTURED' && (
                <button 
                  className="btn-danger"
                  onClick={handleRefund}
                  disabled={actionLoading}
                >
                  {actionLoading ? 'Processing...' : 'Refund Payment'}
                </button>
              )}
            </div>
          </div>

          <div className="detail-grid">
            <div className="detail-item">
              <label>Transaction ID</label>
              <span className="monospace">{transaction.id}</span>
            </div>
            <div className="detail-item">
              <label>Merchant ID</label>
              <span className="monospace">{transaction.merchantId}</span>
            </div>
            <div className="detail-item">
              <label>Created</label>
              <span>{formatDate(transaction.createdAt)}</span>
            </div>
            <div className="detail-item">
              <label>Last Updated</label>
              <span>{formatDate(transaction.updatedAt)}</span>
            </div>
            {transaction.authorizationCode && (
              <div className="detail-item">
                <label>Authorization Code</label>
                <span className="monospace">{transaction.authorizationCode}</span>
              </div>
            )}
            {transaction.captureId && (
              <div className="detail-item">
                <label>Capture ID</label>
                <span className="monospace">{transaction.captureId}</span>
              </div>
            )}
            {transaction.refundId && (
              <div className="detail-item">
                <label>Refund ID</label>
                <span className="monospace">{transaction.refundId}</span>
              </div>
            )}
            {transaction.errorCode && (
              <div className="detail-item error">
                <label>Error Code</label>
                <span className="monospace">{transaction.errorCode}</span>
              </div>
            )}
            {transaction.errorMessage && (
              <div className="detail-item error full-width">
                <label>Error Message</label>
                <span>{transaction.errorMessage}</span>
              </div>
            )}
          </div>

          {transaction.metadata && Object.keys(transaction.metadata).length > 0 && (
            <div className="metadata-section">
              <h3>Metadata</h3>
              <pre>{JSON.stringify(transaction.metadata, null, 2)}</pre>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
