import { 
  PaymentRequest, 
  PaymentResponse, 
  Transaction, 
  TransactionFilter,
  ApiError 
} from '../types'

class ApiClient {
  protected baseUrl: string

  constructor(baseUrl: string) {
    this.baseUrl = baseUrl
  }

  protected async request<T>(
    endpoint: string,
    options: RequestInit,
    token: string
  ): Promise<T> {
    const url = `${this.baseUrl}${endpoint}`
    
    const response = await fetch(url, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`,
        ...options.headers,
      },
    })

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({})) as { error?: ApiError }
      const error = errorData.error || {
        code: 'UNKNOWN_ERROR',
        message: `Request failed with status ${response.status}`,
        correlationId: response.headers.get('X-Correlation-ID') || 'unknown',
        timestamp: new Date().toISOString(),
      }
      throw new Error(error.message)
    }

    return response.json()
  }
}

class PaymentApi extends ApiClient {
  constructor() {
    super(import.meta.env.VITE_API_BASE_URL || '/api/v1')
  }

  async authorize(request: PaymentRequest, token: string): Promise<PaymentResponse> {
    return this.request<PaymentResponse>(
      '/payments/authorize',
      {
        method: 'POST',
        body: JSON.stringify(request),
      },
      token
    )
  }

  async capture(transactionId: string, token: string): Promise<PaymentResponse> {
    return this.request<PaymentResponse>(
      `/payments/${transactionId}/capture`,
      {
        method: 'POST',
      },
      token
    )
  }

  async refund(transactionId: string, token: string): Promise<PaymentResponse> {
    return this.request<PaymentResponse>(
      `/payments/${transactionId}/refund`,
      {
        method: 'POST',
      },
      token
    )
  }

  async getTransaction(transactionId: string, token: string): Promise<Transaction> {
    return this.request<Transaction>(
      `/payments/${transactionId}`,
      {
        method: 'GET',
      },
      token
    )
  }

  async listTransactions(
    filters: TransactionFilter,
    token: string
  ): Promise<Transaction[]> {
    const params = new URLSearchParams()
    
    if (filters.status) params.append('status', filters.status)
    if (filters.startDate) params.append('startDate', filters.startDate)
    if (filters.endDate) params.append('endDate', filters.endDate)
    if (filters.minAmount) params.append('minAmount', filters.minAmount.toString())
    if (filters.maxAmount) params.append('maxAmount', filters.maxAmount.toString())

    const queryString = params.toString()
    const endpoint = queryString ? `/payments?${queryString}` : '/payments'

    // Backend returns a Spring Page object, extract the content array
    const response = await this.request<{ content: Transaction[] }>(
      endpoint,
      {
        method: 'GET',
      },
      token
    )
    
    return response.content || []
  }
}

export const paymentApi = new PaymentApi()
