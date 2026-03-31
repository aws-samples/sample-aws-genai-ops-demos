export interface Product {
  id: string
  name: string
  description: string
  price: number
  currency: string
  imageUrl: string
  category: string
}

export interface CartItem {
  product: Product
  quantity: number
}

export interface Transaction {
  id: string
  merchantId: string
  amount: number
  currency: string
  status: TransactionStatus
  paymentMethodToken: string
  authorizationCode?: string
  captureId?: string
  refundId?: string
  errorCode?: string
  errorMessage?: string
  metadata?: Record<string, unknown>
  createdAt: string
  updatedAt: string
}

export type TransactionStatus = 
  | 'PENDING'
  | 'AUTHORIZED' 
  | 'CAPTURED' 
  | 'REFUNDED' 
  | 'CANCELLED' 
  | 'FAILED'

export interface PaymentRequest {
  amount: number
  currency: string
  paymentMethodToken: string
  metadata?: Record<string, unknown>
}

export interface PaymentResponse {
  id: string
  status: TransactionStatus
  correlationId?: string
  errorCode?: string
  errorMessage?: string
}

export interface TransactionFilter {
  status?: TransactionStatus
  startDate?: string
  endDate?: string
  minAmount?: number
  maxAmount?: number
}

export interface ApiError {
  code: string
  message: string
  correlationId: string
  timestamp: string
  details?: Array<{ field: string; issue: string }>
}
