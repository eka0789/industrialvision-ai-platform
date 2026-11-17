import { useRef, useState } from 'react'
import type { FormEvent, MouseEvent as ReactMouseEvent } from 'react'
import { Check, Layers, Package, Plus, Sliders, Sparkles, Trash2 } from 'lucide-react'
import { cameraApi, modelApi, recipeApi, skuApi } from '../api'
import { Button, EmptyState, ErrorState, LoadingBlock, PageHeader, StatusBadge } from '../components/Ui'
import { useApi } from '../hooks/useApi'
import { useAuth } from '../hooks/useAuth'
import { useToast } from '../hooks/useToast'
import type { InspectionRecipe, ROIZoneSpec } from '../types'
import { errorMessage } from '../utils/errors'

const INITIAL_ZONES: ROIZoneSpec[] = [
  {
    zone_id: 'roi_1_seal',
    zone_name: 'ROI 1 — Heat-Seal Atas',
    x: 0.05,
    y: 0.03,
    width: 0.90,
    height: 0.19,
    bbox: [0.05, 0.03, 0.95, 0.22],
    task_type: 'anomaly_segmentation',
    confidence_threshold: 0.58,
    defect_category: 'SEAL_DEFECT',
    enabled: true,
  },
  {
    zone_id: 'roi_2_expiry',
    zone_name: 'ROI 2 — Area Kode EXP/BB',
    x: 0.54,
    y: 0.25,
    width: 0.40,
    height: 0.19,
    bbox: [0.54, 0.25, 0.94, 0.44],
    task_type: 'ocr_expiry',
    confidence_threshold: 0.55,
    defect_category: 'MISSING_EXP_DATE',
    enabled: true,
  },
  {
    zone_id: 'roi_3_product',
    zone_name: 'ROI 3 — Jendela Produk Makanan',
    x: 0.10,
    y: 0.46,
    width: 0.80,
    height: 0.48,
    bbox: [0.10, 0.46, 0.90, 0.94],
    task_type: 'object_detection',
    confidence_threshold: 0.50,
    defect_category: 'BURNT_OR_BROKEN',
    enabled: true,
  },
]

const ZONE_COLORS: Record<string, string> = {
  SEAL_DEFECT: '#d6f779',
  MISSING_EXP_DATE: '#78d5f7',
  BURNT_OR_BROKEN: '#f7b955',
  FOREIGN_MATTER: '#ff6b6b',
}

export default function RecipesPage() {
  const { user } = useAuth()
  const toast = useToast()
  const skus = useApi(() => skuApi.list(), [])
  const recipes = useApi(() => recipeApi.list(), [])
  const cameras = useApi(() => cameraApi.list(), [])
  const models = useApi(() => modelApi.list(), [])

  const canWrite = user?.role === 'owner' || user?.role === 'admin' || user?.role === 'ai_engineer'
  const canActivate = canWrite || user?.role === 'operator'

  // SKU Form state
  const [skuCode, setSkuCode] = useState('')
  const [skuName, setSkuName] = useState('')
  const [skuCategory, setSkuCategory] = useState('bakery')
  const [packagingType, setPackagingType] = useState('flow_wrap')
  const [savingSku, setSavingSku] = useState(false)

  // Recipe + Interactive SVG ROI Editor state
  const [editingRecipeId, setEditingRecipeId] = useState<number | null>(null)
  const [recipeName, setRecipeName] = useState('Recipe Standar Flow-Wrap Multi-Zone')
  const [selectedSkuId, setSelectedSkuId] = useState<string>('')
  const [selectedCameraId, setSelectedCameraId] = useState<string>('')
  const [claheEnabled, setClaheEnabled] = useState(true)
  const [whiteBalanceEnabled, setWhiteBalanceEnabled] = useState(true)
  const [hsvMaskEnabled, setHsvMaskEnabled] = useState(false)
  const [gamma, setGamma] = useState(1.2)
  const [blurThreshold, setBlurThreshold] = useState(80)
  const [minLuminance, setMinLuminance] = useState(15)
  const [zones, setZones] = useState<ROIZoneSpec[]>(INITIAL_ZONES)
  const [selectedZoneIdx, setSelectedZoneIdx] = useState<number>(0)
  const [savingRecipe, setSavingRecipe] = useState(false)
  const [activatingId, setActivatingId] = useState<number | null>(null)

  const svgRef = useRef<SVGSVGElement | null>(null)
  const [dragMode, setDragMode] = useState<'move' | 'resize' | null>(null)
  const [dragOffset, setDragOffset] = useState<{ dx: number; dy: number }>({ dx: 0, dy: 0 })

  const activeZone = zones[selectedZoneIdx] ?? zones[0]

  const createSku = async (e: FormEvent) => {
    e.preventDefault()
    if (!skuCode.trim() || !skuName.trim()) return
    setSavingSku(true)
    try {
      await skuApi.create({
        sku_code: skuCode.trim().toUpperCase(),
        name: skuName.trim(),
        category: skuCategory,
        packaging_type: packagingType,
      })
      setSkuCode('')
      setSkuName('')
      skus.reload()
      toast('Master ProductSKU berhasil ditambahkan.')
    } catch (reason) {
      toast(errorMessage(reason), 'warning')
    } finally {
      setSavingSku(false)
    }
  }

  const updateZone = (idx: number, patch: Partial<ROIZoneSpec>) => {
    setZones((prev) =>
      prev.map((z, i) => {
        if (i !== idx) return z
        const nextX = Math.max(0, Math.min(0.95, patch.x ?? z.x))
        const nextY = Math.max(0, Math.min(0.95, patch.y ?? z.y))
        const nextW = Math.max(0.05, Math.min(1 - nextX, patch.width ?? z.width))
        const nextH = Math.max(0.05, Math.min(1 - nextY, patch.height ?? z.height))
        return {
          ...z,
          ...patch,
          x: Number(nextX.toFixed(3)),
          y: Number(nextY.toFixed(3)),
          width: Number(nextW.toFixed(3)),
          height: Number(nextH.toFixed(3)),
          bbox: [
            Number(nextX.toFixed(3)),
            Number(nextY.toFixed(3)),
            Number((nextX + nextW).toFixed(3)),
            Number((nextY + nextH).toFixed(3)),
          ],
        }
      }),
    )
  }

  const addZone = () => {
    const nextIdx = zones.length + 1
    const newZone: ROIZoneSpec = {
      zone_id: `roi_${nextIdx}_custom`,
      zone_name: `ROI ${nextIdx} — Zona Baru`,
      x: 0.15,
      y: 0.15,
      width: 0.50,
      height: 0.25,
      bbox: [0.15, 0.15, 0.65, 0.40],
      task_type: 'object_detection',
      confidence_threshold: 0.55,
      defect_category: 'FOREIGN_MATTER',
      enabled: true,
    }
    setZones((prev) => [...prev, newZone])
    setSelectedZoneIdx(zones.length)
  }

  const removeZone = (idx: number) => {
    if (zones.length <= 1) return
    setZones((prev) => prev.filter((_, i) => i !== idx))
    setSelectedZoneIdx(0)
  }

  const toNormalizedCoords = (e: ReactMouseEvent<SVGSVGElement>) => {
    const svg = svgRef.current
    if (!svg) return { nx: 0, ny: 0 }
    const rect = svg.getBoundingClientRect()
    const nx = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width))
    const ny = Math.max(0, Math.min(1, (e.clientY - rect.top) / rect.height))
    return { nx, ny }
  }

  const onSvgMouseMove = (e: ReactMouseEvent<SVGSVGElement>) => {
    if (!dragMode || !activeZone) return
    const { nx, ny } = toNormalizedCoords(e)
    if (dragMode === 'move') {
      const targetX = Math.max(0, Math.min(1 - activeZone.width, nx - dragOffset.dx))
      const targetY = Math.max(0, Math.min(1 - activeZone.height, ny - dragOffset.dy))
      updateZone(selectedZoneIdx, { x: targetX, y: targetY })
    } else if (dragMode === 'resize') {
      const targetW = Math.max(0.05, Math.min(1 - activeZone.x, nx - activeZone.x))
      const targetH = Math.max(0.05, Math.min(1 - activeZone.y, ny - activeZone.y))
      updateZone(selectedZoneIdx, { width: targetW, height: targetH })
    }
  }

  const loadRecipeIntoEditor = (recipe: InspectionRecipe) => {
    setEditingRecipeId(recipe.id)
    setRecipeName(recipe.name)
    setSelectedSkuId(recipe.sku_id ? String(recipe.sku_id) : '')
    setSelectedCameraId(recipe.camera_id ? String(recipe.camera_id) : '')
    const prep = recipe.preprocessing_config ?? recipe.preprocessing_json
    if (prep) {
      setClaheEnabled(Boolean(prep.clahe_enabled ?? true))
      setWhiteBalanceEnabled(Boolean(prep.white_balance_enabled ?? true))
      setHsvMaskEnabled(Boolean(prep.hsv_mask_enabled ?? false))
      setGamma(Number(prep.gamma ?? 1.2))
      setBlurThreshold(Number(prep.blur_threshold ?? 80))
      setMinLuminance(Number(prep.min_luminance ?? 15))
    }
    const rZones = recipe.roi_zones ?? recipe.zones_json
    if (rZones?.length) {
      setZones(rZones)
      setSelectedZoneIdx(0)
    }
    toast(`Recipe "${recipe.name}" dimuat ke Interactive SVG ROI Editor.`)
  }

  const saveRecipe = async (e: FormEvent) => {
    e.preventDefault()
    if (!recipeName.trim()) return
    setSavingRecipe(true)
    try {
      const prepPayload = {
        enabled: true,
        clahe_enabled: claheEnabled,
        white_balance_enabled: whiteBalanceEnabled,
        hsv_mask_enabled: hsvMaskEnabled,
        gamma: Number(gamma),
        blur_threshold: Number(blurThreshold),
        min_luminance: Number(minLuminance),
      }
      if (editingRecipeId) {
        await recipeApi.update(editingRecipeId, {
          name: recipeName.trim(),
          camera_id: selectedCameraId ? Number(selectedCameraId) : null,
          preprocessing_config: prepPayload,
          roi_zones: zones,
        })
        toast('Perubahan InspectionRecipe & Multi-Zone ROI berhasil disimpan.')
      } else {
        const chosenSku = (skus.data ?? []).find((s) => String(s.id) === selectedSkuId)
        await recipeApi.create({
          sku_id: selectedSkuId ? Number(selectedSkuId) : null,
          sku_code: chosenSku?.sku_code ?? `SKU-${Date.now().toString().slice(-4)}`,
          name: recipeName.trim(),
          camera_id: selectedCameraId ? Number(selectedCameraId) : null,
          is_active: true,
          preprocessing_config: prepPayload,
          roi_zones: zones,
        })
        toast('InspectionRecipe Multi-Zone ROI baru berhasil dibuat & diaktifkan.')
      }
      setEditingRecipeId(null)
      recipes.reload()
    } catch (reason) {
      toast(errorMessage(reason), 'warning')
    } finally {
      setSavingRecipe(false)
    }
  }

  const activateRecipe = async (recipeId: number) => {
    setActivatingId(recipeId)
    try {
      const updated = await recipeApi.activate(recipeId)
      recipes.reload()
      toast(`Changeover berhasil: "${updated.name}" (${updated.sku_code ?? 'SKU'}) kini AKTIF pada kamera #${updated.camera_id ?? 'Lini'}.`)
    } catch (reason) {
      toast(errorMessage(reason), 'warning')
    } finally {
      setActivatingId(null)
    }
  }

  return (
    <div className="page recipes-page">
      <PageHeader
        eyebrow="FOOD DIVISION / SKU RECIPES & MULTI-ZONE ROI"
        title={<>Satu lini kemasan.<br /><span className="text-muted">Multi-titik kendali mutu.</span></>}
        description="Kelola katalog ProductSKU, rancang kotak Multi-Zone ROI (Seal PatchCore, Expiry OCR, Product HSV+YOLO) secara visual, dan lakukan changeover instan."
      />

      <div className="overview-columns" style={{ alignItems: 'start', marginBottom: '1.5rem' }}>
        {/* Panel 1: Interactive SVG ROI Editor */}
        <section className="panel" aria-label="Interactive SVG ROI Editor" style={{ padding: '1.1rem' }}>
          <header className="panel-header" style={{ padding: '0 0 0.85rem 0' }}>
            <div>
              <p className="eyebrow"><Layers size={14} /> INTERACTIVE SVG ROI EDITOR</p>
              <h2>{editingRecipeId ? `Edit Recipe #${editingRecipeId}` : 'Desainer Multi-Zone ROI Kemasan'}</h2>
            </div>
            {canWrite && (
              <Button type="button" variant="secondary" onClick={addZone}>
                <Plus size={15} />Tambah Zona ROI
              </Button>
            )}
          </header>

          <p style={{ fontSize: '0.82rem', marginBottom: '0.65rem' }}>
            Klik dan geser kotak di atas pratinjau kemasan untuk mengatur posisi <code>(x, y)</code>, atau tarik titik sudut kanan-bawah untuk mengubah ukuran <code>(width, height)</code> dalam koordinat ternormalisasi <code>0.0 – 1.0</code>.
          </p>

          <div
            style={{
              background: '#1c211b',
              borderRadius: '10px',
              border: '1px solid rgba(214, 247, 121, 0.28)',
              padding: '0.75rem',
              marginBottom: '1rem',
            }}
          >
            <svg
              ref={svgRef}
              viewBox="0 0 600 380"
              role="img"
              aria-label="Kanvas interaktif editor Multi-Zone ROI kemasan makanan"
              style={{ width: '100%', height: 'auto', display: 'block', userSelect: 'none', touchAction: 'none', cursor: dragMode ? 'grabbing' : 'default' }}
              onMouseMove={onSvgMouseMove}
              onMouseUp={() => setDragMode(null)}
              onMouseLeave={() => setDragMode(null)}
            >
              {/* Simulated Food Packaging Flow-Wrap Graphic */}
              <rect x="0" y="0" width="600" height="380" fill="#141813" rx="6" />
              <rect x="28" y="8" width="544" height="364" fill="#232922" stroke="#495446" strokeWidth="2" rx="8" />
              {/* Top & Bottom Heat-Seal Fin Ridges */}
              {Array.from({ length: 34 }, (_, i) => (
                <line key={i} x1={40 + i * 15} y1="12" x2={40 + i * 15} y2="75" stroke="#3a4337" strokeWidth="2" />
              ))}
              {/* Expiry Date Print Box Preview */}
              <rect x="330" y="96" width="225" height="68" fill="#f3f4ed" rx="4" />
              <text x="344" y="126" fill="#1c211b" fontSize="13" fontFamily="monospace" fontWeight="bold">
                EXP 17.11.27
              </text>
              <text x="344" y="148" fill="#1c211b" fontSize="12" fontFamily="monospace">
                LOT B01-SHIFT1
              </text>
              {/* Product Window Preview */}
              <rect x="65" y="178" width="470" height="175" fill="#2c241b" stroke="#614e3a" strokeWidth="1.5" rx="8" />
              <text x="84" y="205" fill="#f3f4ed" fontSize="12" opacity="0.75">
                AREA PRODUK PANGAN (HSV Color Histogram + YOLOv8 Foreign Matter)
              </text>

              {/* Interactive ROI Boxes */}
              {zones.map((zone, idx) => {
                const bx = zone.x * 600
                const by = zone.y * 380
                const bw = zone.width * 600
                const bh = zone.height * 380
                const color = ZONE_COLORS[zone.defect_category] ?? '#d6f779'
                const isSelected = idx === selectedZoneIdx
                return (
                  <g key={zone.zone_id}>
                    <rect
                      x={bx}
                      y={by}
                      width={bw}
                      height={bh}
                      fill={color}
                      fillOpacity={isSelected ? 0.22 : 0.10}
                      stroke={color}
                      strokeWidth={isSelected ? 3 : 1.75}
                      strokeDasharray={isSelected ? undefined : '5 3'}
                      style={{ cursor: canWrite ? 'move' : 'pointer' }}
                      onMouseDown={(e) => {
                        e.stopPropagation()
                        setSelectedZoneIdx(idx)
                        if (!canWrite) return
                        const { nx, ny } = toNormalizedCoords(e as unknown as ReactMouseEvent<SVGSVGElement>)
                        setDragOffset({ dx: nx - zone.x, dy: ny - zone.y })
                        setDragMode('move')
                      }}
                    />
                    <rect x={bx} y={Math.max(0, by - 22)} width={Math.min(bw, 290)} height="22" fill={color} rx="3" />
                    <text x={bx + 6} y={Math.max(15, by - 7)} fill="#1c211b" fontSize="11" fontWeight="bold">
                      {zone.zone_name} · [{zone.defect_category}]
                    </text>
                    {canWrite && isSelected && (
                      <circle
                        cx={bx + bw}
                        cy={by + bh}
                        r="8"
                        fill="#d6f779"
                        stroke="#1c211b"
                        strokeWidth="2"
                        style={{ cursor: 'nwse-resize' }}
                        onMouseDown={(e) => {
                          e.stopPropagation()
                          setSelectedZoneIdx(idx)
                          setDragMode('resize')
                        }}
                      />
                    )}
                  </g>
                )
              })}
            </svg>
          </div>

          {/* Zone Selector Pills */}
          <div style={{ display: 'flex', gap: '0.45rem', flexWrap: 'wrap', marginBottom: '0.85rem' }}>
            {zones.map((z, idx) => (
              <Button
                key={z.zone_id}
                type="button"
                variant={idx === selectedZoneIdx ? 'primary' : 'secondary'}
                onClick={() => setSelectedZoneIdx(idx)}
              >
                {z.zone_name} ({z.task_type})
              </Button>
            ))}
          </div>

          {/* Active Zone Property Inspector */}
          {activeZone && (
            <div className="form-grid form-grid--two" style={{ marginBottom: '1rem', padding: '0.8rem', border: '1px solid var(--border, #2a2e37)', borderRadius: '8px' }}>
              <label className="field">
                <span>Nama Zona ROI</span>
                <input
                  value={activeZone.zone_name}
                  disabled={!canWrite}
                  onChange={(e) => updateZone(selectedZoneIdx, { zone_name: e.target.value })}
                />
              </label>
              <label className="field">
                <span>Task Type</span>
                <select
                  value={activeZone.task_type}
                  disabled={!canWrite}
                  onChange={(e) =>
                    updateZone(selectedZoneIdx, {
                      task_type: e.target.value as ROIZoneSpec['task_type'],
                    })
                  }
                >
                  <option value="anomaly_segmentation">anomaly_segmentation (PatchCore Seal)</option>
                  <option value="ocr_expiry">ocr_expiry (PaddleOCR / Tesseract EXP Date)</option>
                  <option value="object_detection">object_detection (HSV + YOLOv8 Product)</option>
                </select>
              </label>
              <label className="field">
                <span>Kategori Defect</span>
                <select
                  value={activeZone.defect_category}
                  disabled={!canWrite}
                  onChange={(e) => updateZone(selectedZoneIdx, { defect_category: e.target.value })}
                >
                  <option value="SEAL_DEFECT">SEAL_DEFECT (Kebocoran Seal)</option>
                  <option value="MISSING_EXP_DATE">MISSING_EXP_DATE (Tanpa Kode EXP)</option>
                  <option value="BURNT_OR_BROKEN">BURNT_OR_BROKEN (Gosong / Patah)</option>
                  <option value="FOREIGN_MATTER">FOREIGN_MATTER (Kontaminasi Benda Asing)</option>
                </select>
              </label>
              <label className="field">
                <span>Model AI Target</span>
                <select
                  value={activeZone.model_id ?? ''}
                  disabled={!canWrite}
                  onChange={(e) => updateZone(selectedZoneIdx, { model_id: e.target.value ? Number(e.target.value) : null })}
                >
                  <option value="">Default Stasiun / Built-in OCR</option>
                  {(models.data?.items ?? []).map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.name} ({m.task_type ?? m.framework})
                    </option>
                  ))}
                </select>
              </label>
              <label className="field">
                <span>Koordinat X (0.0–1.0)</span>
                <input
                  type="number"
                  step="0.01"
                  min="0"
                  max="0.95"
                  disabled={!canWrite}
                  value={activeZone.x}
                  onChange={(e) => updateZone(selectedZoneIdx, { x: Number(e.target.value) })}
                />
              </label>
              <label className="field">
                <span>Koordinat Y (0.0–1.0)</span>
                <input
                  type="number"
                  step="0.01"
                  min="0"
                  max="0.95"
                  disabled={!canWrite}
                  value={activeZone.y}
                  onChange={(e) => updateZone(selectedZoneIdx, { y: Number(e.target.value) })}
                />
              </label>
              <label className="field">
                <span>Lebar / Width (0.05–1.0)</span>
                <input
                  type="number"
                  step="0.01"
                  min="0.05"
                  max="1"
                  disabled={!canWrite}
                  value={activeZone.width}
                  onChange={(e) => updateZone(selectedZoneIdx, { width: Number(e.target.value) })}
                />
              </label>
              <label className="field">
                <span>Tinggi / Height (0.05–1.0)</span>
                <input
                  type="number"
                  step="0.01"
                  min="0.05"
                  max="1"
                  disabled={!canWrite}
                  value={activeZone.height}
                  onChange={(e) => updateZone(selectedZoneIdx, { height: Number(e.target.value) })}
                />
              </label>
              <label className="field">
                <span>Confidence / Threshold</span>
                <input
                  type="number"
                  step="0.01"
                  min="0.05"
                  max="0.99"
                  disabled={!canWrite}
                  value={activeZone.confidence_threshold}
                  onChange={(e) => updateZone(selectedZoneIdx, { confidence_threshold: Number(e.target.value) })}
                />
              </label>
              {canWrite && zones.length > 1 && (
                <div className="field" style={{ alignSelf: 'end' }}>
                  <Button type="button" variant="ghost" onClick={() => removeZone(selectedZoneIdx)}>
                    <Trash2 size={15} />Hapus Zona Ini
                  </Button>
                </div>
              )}
            </div>
          )}

          {/* Recipe & Preprocessing Save Form */}
          <form onSubmit={saveRecipe} className="form-grid form-grid--two">
            <label className="field">
              <span>Nama InspectionRecipe <b>*</b></span>
              <input
                required
                disabled={!canWrite}
                value={recipeName}
                onChange={(e) => setRecipeName(e.target.value)}
                placeholder="mis. Recipe Biskuit Cokelat 100g Lini 1"
              />
            </label>
            <label className="field">
              <span>Tautkan ProductSKU</span>
              <select value={selectedSkuId} disabled={!canWrite} onChange={(e) => setSelectedSkuId(e.target.value)}>
                <option value="">Pilih SKU Produk…</option>
                {(skus.data ?? []).map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.sku_code} — {s.name} ({s.category})
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Stasiun Kamera Target</span>
              <select value={selectedCameraId} disabled={!canWrite} onChange={(e) => setSelectedCameraId(e.target.value)}>
                <option value="">Semua Kamera Lini</option>
                {(cameras.data ?? []).map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name} (#{c.id})
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Gamma, Laplacian Blur & Min Luminance</span>
              <div style={{ display: 'flex', gap: '0.4rem' }}>
                <input
                  type="number"
                  step="0.05"
                  min="0.5"
                  max="2.5"
                  disabled={!canWrite}
                  value={gamma}
                  onChange={(e) => setGamma(Number(e.target.value))}
                  aria-label="Gamma Correction"
                  title="Gamma Correction"
                />
                <input
                  type="number"
                  step="5"
                  min="10"
                  max="500"
                  disabled={!canWrite}
                  value={blurThreshold}
                  onChange={(e) => setBlurThreshold(Number(e.target.value))}
                  aria-label="Laplacian Blur Threshold"
                  title="Laplacian Blur Threshold (LENS_BLUR_OR_FOG)"
                />
                <input
                  type="number"
                  step="5"
                  min="5"
                  max="120"
                  disabled={!canWrite}
                  value={minLuminance}
                  onChange={(e) => setMinLuminance(Number(e.target.value))}
                  aria-label="Minimum Luminance Guard"
                  title="Minimum Luminance (LIGHTING_FAILURE Guard)"
                />
              </div>
            </label>
            <div className="field field--full" style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap' }}>
              <label className="checkbox-field">
                <input
                  type="checkbox"
                  disabled={!canWrite}
                  checked={claheEnabled}
                  onChange={(e) => setClaheEnabled(e.target.checked)}
                />
                <span>CLAHE Glare Suppression (LAB L-Channel)</span>
              </label>
              <label className="checkbox-field">
                <input
                  type="checkbox"
                  disabled={!canWrite}
                  checked={whiteBalanceEnabled}
                  onChange={(e) => setWhiteBalanceEnabled(e.target.checked)}
                />
                <span>Gray-World White Balance (Inter-Shift Normalization)</span>
              </label>
              <label className="checkbox-field">
                <input
                  type="checkbox"
                  disabled={!canWrite}
                  checked={hsvMaskEnabled}
                  onChange={(e) => setHsvMaskEnabled(e.target.checked)}
                />
                <span>HSV Color Masking (Bakery Bake Maturity)</span>
              </label>
            </div>
            {canWrite && (
              <div style={{ display: 'flex', gap: '0.5rem', justifyContent: 'flex-end', alignItems: 'center' }}>
                {editingRecipeId && (
                  <Button type="button" variant="ghost" onClick={() => setEditingRecipeId(null)}>
                    Batal Edit
                  </Button>
                )}
                <Button type="submit" loading={savingRecipe}>
                  <Sparkles size={15} />
                  {editingRecipeId ? 'Simpan Perubahan Recipe' : 'Simpan & Aktifkan Recipe'}
                </Button>
              </div>
            )}
          </form>
        </section>

        {/* Panel 2: SKU Master & Active Recipes Changeover */}
        <div style={{ display: 'grid', gap: '1.25rem' }}>
          <section className="panel" aria-label="Daftar InspectionRecipe & Changeover Lini" style={{ padding: '1.1rem' }}>
            <header className="panel-header" style={{ padding: '0 0 0.75rem 0' }}>
              <div>
                <p className="eyebrow"><Sliders size={14} /> ATOMIC CHANGEOVER / 1 ACTIVE PER CAMERA</p>
                <h2>Daftar Recipe & Ganti SKU Cepat</h2>
              </div>
            </header>
            {recipes.isLoading && <LoadingBlock label="Memuat daftar recipe…" />}
            {recipes.error && <ErrorState message={recipes.error} retry={recipes.reload} />}
            {!recipes.isLoading && !(recipes.data ?? []).length && (
              <EmptyState title="Belum ada recipe" description="Buat recipe pertama di editor sebelah kiri." />
            )}
            <ul className="document-list">
              {(recipes.data ?? []).map((r) => (
                <li className="document-item" key={r.id} data-testid={`recipe-${r.id}`}>
                  <div className="document-info">
                    <strong>{r.name}</strong>
                    <small>
                      SKU: <code>{r.sku_code ?? '—'}</code> · Kamera: #{r.camera_id ?? 'Semua'} · {(r.roi_zones ?? r.zones_json).length} Zona ROI
                    </small>
                  </div>
                  <StatusBadge value={r.is_active ? 'active' : 'inactive'} />
                  <div className="document-actions" style={{ display: 'flex', gap: '0.4rem', flexWrap: 'wrap' }}>
                    <Button variant="secondary" onClick={() => loadRecipeIntoEditor(r)}>
                      Buka ROI
                    </Button>
                    {canActivate && !r.is_active && (
                      <Button
                        loading={activatingId === r.id}
                        onClick={() => activateRecipe(r.id)}
                      >
                        <Check size={14} />Changeover
                      </Button>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          </section>

          <section className="panel" aria-label="Katalog Master ProductSKU" style={{ padding: '1.1rem' }}>
            <header className="panel-header" style={{ padding: '0 0 0.75rem 0' }}>
              <div>
                <p className="eyebrow"><Package size={14} /> MASTER DATA / PRODUCT SKU</p>
                <h2>Katalog Varian Produk (ProductSKU)</h2>
              </div>
            </header>
            {canWrite && (
              <form onSubmit={createSku} className="form-grid form-grid--two" style={{ marginBottom: '0.9rem' }}>
                <label className="field">
                  <span>Kode SKU <b>*</b></span>
                  <input
                    required
                    value={skuCode}
                    onChange={(e) => setSkuCode(e.target.value)}
                    placeholder="mis. BIS-COK-100G"
                  />
                </label>
                <label className="field">
                  <span>Nama Produk <b>*</b></span>
                  <input
                    required
                    value={skuName}
                    onChange={(e) => setSkuName(e.target.value)}
                    placeholder="mis. Biskuit Cokelat 100g"
                  />
                </label>
                <label className="field">
                  <span>Kategori Pangan</span>
                  <select value={skuCategory} onChange={(e) => setSkuCategory(e.target.value)}>
                    <option value="bakery">Bakery & Biscuit</option>
                    <option value="dairy">Dairy & UHT</option>
                    <option value="beverage">Beverage / Minuman</option>
                    <option value="snack">Snack / Makanan Ringan</option>
                  </select>
                </label>
                <label className="field">
                  <span>Jenis Kemasan</span>
                  <select value={packagingType} onChange={(e) => setPackagingType(e.target.value)}>
                    <option value="flow_wrap">Plastic Flow-Wrap</option>
                    <option value="foil_pouch">Aluminium Foil Standing Pouch</option>
                    <option value="aseptic_carton">Aseptic Carton / Tetra</option>
                    <option value="bottle_can">Bottle / Can</option>
                  </select>
                </label>
                <div className="field field--full" style={{ display: 'flex', justifyContent: 'flex-end' }}>
                  <Button type="submit" variant="secondary" loading={savingSku}>
                    <Plus size={15} />Tambah ProductSKU
                  </Button>
                </div>
              </form>
            )}
            <ul className="document-list">
              {(skus.data ?? []).map((s) => (
                <li className="document-item" key={s.id}>
                  <div className="document-info">
                    <strong>{s.sku_code} — {s.name}</strong>
                    <small>Kategori: {s.category} · Kemasan: {s.packaging_type}</small>
                  </div>
                  <StatusBadge value={s.is_active ? 'active' : 'inactive'} />
                </li>
              ))}
            </ul>
          </section>
        </div>
      </div>
    </div>
  )
}
