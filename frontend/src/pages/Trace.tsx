import React, { useEffect, useMemo, useState } from 'react';
import { useLocation, useNavigate, Link } from 'react-router-dom';
import { Search, Loader2, Copy, Check, ArrowLeft, AlertTriangle, FileQuestion, ShieldAlert } from 'lucide-react';
import { analysisApi } from '../services/api';
import type { BatchTraceability } from '../types';
import {
  canonicalizeBatchNumber,
  BatchNumberFormatError,
  strictEncodeURIComponent,
  extractNFromUrl,
} from '../utils/batchNumber';

type FailureKind = 'not-found' | 'invalid' | 'conflict' | 'network' | null;

const FailurePanel: React.FC<{ kind: Exclude<FailureKind, null>; detail?: string; n?: string }> = ({ kind, detail, n }) => {
  const config = {
    'not-found': {
      icon: FileQuestion,
      title: '未找到该批次',
      desc: `批次号 “${n}” 不存在。请核对追溯标签上的批次号，或联系客服确认。`,
      color: 'text-gray-600 bg-gray-50 border-gray-200',
    },
    invalid: {
      icon: AlertTriangle,
      title: '批次号格式非法',
      desc: detail || '批次号不能为空、超长或含有非法控制字符。',
      color: 'text-amber-700 bg-amber-50 border-amber-200',
    },
    conflict: {
      icon: ShieldAlert,
      title: '批次号存在规范化冲突',
      desc: detail || '该批次号与已有批次号仅大小写不同。批次号大小写敏感，请勿混用大小写。',
      color: 'text-red-700 bg-red-50 border-red-200',
    },
    network: {
      icon: AlertTriangle,
      title: '查询失败',
      desc: '网络或服务异常，请稍后重试。',
      color: 'text-amber-700 bg-amber-50 border-amber-200',
    },
  }[kind];
  const Icon = config.icon;
  return (
    <div className={`card border ${config.color}`}>
      <div className="flex items-start space-x-3">
        <Icon size={24} />
        <div>
          <h2 className="text-lg font-semibold">{config.title}</h2>
          <p className="mt-1 text-sm opacity-90">{config.desc}</p>
        </div>
      </div>
    </div>
  );
};

const Section: React.FC<{ title: string; rows: Array<Record<string, unknown>>; columns: Array<{ key: string; label: string }> }> = ({ title, rows, columns }) => (
  <div className="mt-5">
    <h4 className="font-medium text-gray-700 mb-2">
      {title} ({rows.length})
    </h4>
    {rows.length === 0 ? (
      <p className="text-gray-500 text-sm">暂无记录</p>
    ) : (
      <div className="overflow-x-auto">
        <table className="table text-sm">
          <thead>
            <tr>{columns.map((c) => <th key={c.key}>{c.label}</th>)}</tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i}>{columns.map((c) => <td key={c.key}>{String(r[c.key] ?? '-')}</td>)}</tr>
            ))}
          </tbody>
        </table>
      </div>
    )}
  </div>
);

const Trace: React.FC = () => {
  const location = useLocation();
  const navigate = useNavigate();

  // 兼容旧的路径形态 /trace/<批次号>?其它参数：提取路径段并归一到查询形态
  const legacyPathSegment = useMemo(() => {
    if (location.pathname === '/trace') return null;
    return decodeURIComponent(location.pathname.replace(/^\/trace\/+/, '').replace(/\/+$/, ''));
  }, [location.pathname]);

  const query = useMemo(() => new URLSearchParams(location.search), [location.search]);
  const rawN = query.get('n');
  const otherParams = useMemo(() => {
    const p = new URLSearchParams(location.search);
    p.delete('n');
    return p;
  }, [location.search]);

  const [data, setData] = useState<BatchTraceability | null>(null);
  const [loading, setLoading] = useState(true);
  const [failure, setFailure] = useState<FailureKind>(null);
  const [failureDetail, setFailureDetail] = useState<string>('');
  const [copied, setCopied] = useState(false);

  // 旧路径链接 → 规范地址（保留查询参数），一次性 replace
  useEffect(() => {
    if (legacyPathSegment === null) return;
    let canonical: string;
    try {
      canonical = canonicalizeBatchNumber(legacyPathSegment);
    } catch {
      canonical = legacyPathSegment; // 交给规范端点判 422
    }
    const suffix = otherParams.toString();
    navigate(`/trace?n=${strictEncodeURIComponent(canonical)}${suffix ? `&${suffix}` : ''}`, { replace: true });
  }, [legacyPathSegment, otherParams, navigate]);

  const canonicalN = useMemo(() => {
    if (rawN === null) return null;
    try {
      return canonicalizeBatchNumber(rawN);
    } catch {
      return null;
    }
  }, [rawN]);

  useEffect(() => {
    if (legacyPathSegment !== null) return; // 等待归一化跳转
    if (rawN === null) {
      setLoading(false);
      setFailure('invalid');
      setFailureDetail('链接缺少批次号参数 n。');
      return;
    }
    let canonical: string;
    try {
      canonical = canonicalizeBatchNumber(rawN);
    } catch (e) {
      setLoading(false);
      setFailure('invalid');
      setFailureDetail(e instanceof BatchNumberFormatError ? e.message : '批次号格式非法');
      return;
    }
    // 非规范输入（首尾空白/NFD 等）先把地址栏替换为规范地址，再查询
    if (canonical !== rawN) {
      const suffix = otherParams.toString();
      navigate(`/trace?n=${strictEncodeURIComponent(canonical)}${suffix ? `&${suffix}` : ''}`, { replace: true });
      return;
    }

    let cancelled = false;
    setLoading(true);
    setFailure(null);
    analysisApi
      .traceByBatchNumber(canonical)
      .then((res) => {
        if (cancelled) return;
        // 经历了 301 别名归一时，axios 的响应 URL 携带当前批次号
        const finalUrl = (res.request as XMLHttpRequest | undefined)?.responseURL;
        const finalN = extractNFromUrl(finalUrl);
        if (finalN && finalN !== canonical) {
          const suffix = otherParams.toString();
          navigate(`/trace?n=${strictEncodeURIComponent(finalN)}${suffix ? `&${suffix}` : ''}`, { replace: true });
        }
        setData(res.data);
      })
      .catch((err) => {
        if (cancelled) return;
        const status = err?.response?.status;
        setData(null);
        if (status === 404) setFailure('not-found');
        else if (status === 422) {
          setFailure('invalid');
          setFailureDetail(String(err?.response?.data?.detail ?? '批次号格式非法'));
        } else if (status === 409) {
          setFailure('conflict');
          setFailureDetail(String(err?.response?.data?.detail ?? ''));
        } else setFailure('network');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rawN, legacyPathSegment]);

  const shareUrl = useMemo(() => {
    if (!canonicalN) return '';
    return `${window.location.origin}/trace?n=${strictEncodeURIComponent(canonicalN)}`;
  }, [canonicalN]);

  const copyShareLink = async () => {
    if (!shareUrl) return;
    try {
      await navigator.clipboard.writeText(shareUrl);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* 剪贴板不可用时静默 */
    }
  };

  if (legacyPathSegment !== null) {
    return (
      <div className="flex items-center justify-center h-64 text-gray-500">
        <Loader2 className="animate-spin mr-2" size={20} /> 正在跳转到规范地址…
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <Link to="/analysis" className="inline-flex items-center text-sm text-ocean-600 hover:underline mb-2">
            <ArrowLeft size={16} className="mr-1" /> 返回养殖分析
          </Link>
          <h1 className="text-2xl font-bold text-gray-900">批次追溯</h1>
          {canonicalN && <p className="text-gray-600 mt-1 font-mono">{canonicalN}</p>}
        </div>
        {canonicalN && !failure && (
          <button onClick={copyShareLink} className="btn-secondary inline-flex items-center space-x-2">
            {copied ? <Check size={18} className="text-green-600" /> : <Copy size={18} />}
            <span>{copied ? '已复制' : '复制规范链接'}</span>
          </button>
        )}
      </div>

      {loading && (
        <div className="flex items-center justify-center h-64 text-gray-500">
          <Loader2 className="animate-spin mr-2" size={24} /> 正在查询追溯信息…
        </div>
      )}

      {!loading && failure && (
        <FailurePanel kind={failure} detail={failureDetail} n={canonicalN ?? rawN ?? ''} />
      )}

      {!loading && !failure && data && (
        <div className="card">
          <h2 className="text-lg font-semibold text-gray-900 mb-4 flex items-center space-x-2">
            <Search size={20} />
            <span>追溯结果 - {data.batch.batch_number}</span>
          </h2>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div className="p-3 bg-blue-50 rounded-lg">
              <p className="text-sm text-blue-600">养殖品种</p>
              <p className="font-semibold">{data.batch.species}</p>
            </div>
            <div className="p-3 bg-purple-50 rounded-lg">
              <p className="text-sm text-purple-600">状态</p>
              <p className="font-semibold">{data.batch.status}</p>
            </div>
            <div className="p-3 bg-green-50 rounded-lg">
              <p className="text-sm text-green-600">放苗日期</p>
              <p className="font-semibold">{data.batch.stocking_date}</p>
            </div>
          </div>

          <Section
            title="投苗记录"
            rows={data.stocking_records as unknown as Array<Record<string, unknown>>}
            columns={[
              { key: 'stocking_date', label: '投苗日期' },
              { key: 'quantity', label: '数量' },
              { key: 'source', label: '来源' },
              { key: 'batch_number', label: '苗种批次号' },
            ]}
          />
          <Section
            title="投喂记录"
            rows={data.feeding_records as unknown as Array<Record<string, unknown>>}
            columns={[
              { key: 'feeding_date', label: '投喂日期' },
              { key: 'feed_type', label: '饲料类型' },
              { key: 'quantity', label: '数量' },
            ]}
          />
          <Section
            title="水质监测"
            rows={data.water_quality_records as unknown as Array<Record<string, unknown>>}
            columns={[
              { key: 'record_date', label: '监测日期' },
              { key: 'water_temperature', label: '水温' },
              { key: 'ph_value', label: 'pH' },
              { key: 'dissolved_oxygen', label: '溶氧' },
            ]}
          />
          <Section
            title="用药记录"
            rows={data.medication_records as unknown as Array<Record<string, unknown>>}
            columns={[
              { key: 'medication_date', label: '用药日期' },
              { key: 'medication_name', label: '药品名称' },
              { key: 'dosage', label: '用量' },
              { key: 'unit', label: '单位' },
            ]}
          />
          <Section
            title="成本记录"
            rows={data.cost_records as unknown as Array<Record<string, unknown>>}
            columns={[
              { key: 'cost_date', label: '日期' },
              { key: 'cost_type', label: '费用类型' },
              { key: 'amount', label: '金额' },
              { key: 'description', label: '说明' },
            ]}
          />
          <Section
            title="销售记录"
            rows={data.harvest_sales as unknown as Array<Record<string, unknown>>}
            columns={[
              { key: 'sale_date', label: '销售日期' },
              { key: 'weight', label: '重量' },
              { key: 'unit_price', label: '单价' },
              { key: 'total_amount', label: '总金额' },
              { key: 'buyer', label: '买家' },
            ]}
          />
        </div>
      )}
    </div>
  );
};

export default Trace;
