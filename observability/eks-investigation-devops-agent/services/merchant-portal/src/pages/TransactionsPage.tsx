import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import { paymentApi } from '../services/api'
import { Transaction, TransactionStatus, TransactionFilter } from '../types'
import './TransactionsPage.css'

const STATUS_OPTIONS: TransactionStatus[] = [
  'PENDING',
  'AUTHORIZED',
  'CAPTURED',
  'REFUNDED',
  'CANCELLED',
  'FAILED',
]

export default function TransactionsPage() {
  const { getAccessToken } = useAuth()
  const [transactions, setTransactions] = useState<Transaction[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState('')
  const [filters, setFilters] = useState<TransactionFilter>({})

  useEffect(() => {
    loadTransactions()
  }, [filters])

  async function loadTransactions() {
    setIsLoading(true)
    setError('')

    try {
      const token = await getAccessToken()
      if (!token) {
        throw new Error('Session expired')
      }

      const data = await paymentApi.listTransactions(filters, token)
      setTransactions(data)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load transactions')
    } finally {
      setIsLoading(false)
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
      dateStyle: 'medium',
      timeStyle: 'short',
    })
  }

  function getStatusClass(status: TransactionStatus) {
    switch (status) {
      case 'CAPTURED':
        return 'status-success'
      case 'AUTHORIZED':
      case 'PENDING':
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

  return (
    <div className="transactions-page">
      <h1>Transaction History</h1>
      
      <div className="filters card">
        <div className="filter-group">
          <label htmlFor="status">Status</label>
          <select
            id="status"
            value={filters.status || ''}
            onChange={(e) => setFilters(prev => ({
              ...prev,
              status: e.target.value as TransactionStatus || undefined,
            }))}
          >
            <option value="">All Statuses</option>
            {STATUS_OPTIONS.map((status) => (
              <option key={status} value={status}>{status}</option>
            ))}
          </select>
        </div>

        <div className="filter-group">
          <label htmlFor="startDate">From Date</label>
          <input
            id="startDate"
            type="date"
            value={filters.startDate || ''}
            onChange={(e) => setFilters(prev => ({
              ...prev,
              startDate: e.target.value || undefined,
            }))}
          />
        </div>

        <div className="filter-group">
          <label htmlFor="endDate">To Date</label>
          <input
            id="endDate"
            type="date"
            value={filters.endDate || ''}
            onChange={(e) => setFilters(prev => ({
              ...prev,
              endDate: e.target.value || undefined,
            }))}
          />
        </div>

        <button 
          className="btn-clear-filters"
          onClick={() => setFilters({})}
        >
          Clear Filters
        </button>
      </div>

      {error && <p className="error-message">{error}</p>}

      {isLoading ? (
        <div className="loading">Loading transactions...</div>
      ) : transactions.length === 0 ? (
        <div className="empty-state card">
          <p>No transactions found</p>
          <Link to="/catalog" className="btn-primary">
            Make a Purchase
          </Link>
        </div>
      ) : (
        <div className="transactions-table-wrapper">
          <table className="transactions-table">
            <thead>
              <tr>
                <th>Transaction ID</th>
                <th>Date</th>
                <th>Amount</th>
                <th>Status</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {transactions.map((tx) => (
                <tr key={tx.id}>
                  <td className="tx-id">{tx.id.substring(0, 8)}...</td>
                  <td>{formatDate(tx.createdAt)}</td>
                  <td>{formatPrice(tx.amount, tx.currency)}</td>
                  <td>
                    <span className={`status-badge ${getStatusClass(tx.status)}`}>
                      {tx.status}
                    </span>
                  </td>
                  <td>
                    <Link to={`/transactions/${tx.id}`} className="btn-view">
                      View
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
