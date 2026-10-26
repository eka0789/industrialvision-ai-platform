import { Link } from 'react-router-dom'

export function Brand({ compact = false }: { compact?: boolean }) {
  return (
    <Link to="/" className={`brand ${compact ? 'brand--compact' : ''}`} aria-label="IndustrialVision AI, ke ringkasan">
      <span className="brand-mark" aria-hidden="true"><i /><i /><i /></span>
      <span className="brand-type">
        <strong>INDUSTRIAL<span>VISION</span></strong>
        {!compact && <small>Quality intelligence / 01</small>}
      </span>
    </Link>
  )
}
