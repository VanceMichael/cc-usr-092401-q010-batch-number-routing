import React, { useEffect, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { Search, Loader2, ArrowLeft, Copy, Check } from 'lucide-react';
import { analysisApi, pondApi } from '../services/api';
import type { BatchTraceability, Pond } from '../types';
import {
  buildBatchTraceShareUrl,
} from '../utils/batchLink';
import BatchTraceResult from '../components/BatchTraceResult';

type ErrorKind = 'invalid' | 'not_found' | 'conflict' | 'unavailable';

const ERROR_TEXT: Record<ErrorKind, string> = {
  invalid: '批次号格式非法：不能为空、包含控制字符或超长，请核对追溯标签后重试。',
  not_found: '未找到该批次，批次号可能已被更正。请联系客服确认最新批次号。',
  conflict: '该批次号与其他批次存在规范化冲突，已被系统拦截，请联系管理员处理。',
  unavailable: '查询服务暂时不可用，请稍后重试。',
};

function classifyError(err: unknown): ErrorKind {
  const status = (err as { response?: { status?: number } })?.response?.status;
  const code = (err as { response?: { data?: { code?: string } } })?.response?.data?.code;
  if (code === 'invalid_format' || status === 422) return 'invalid';
  if (code === 'conflict' || status === 409) return 'conflict';
  if (status === 404) return 'not_found';
  return 'unavailable';
}

const TracePage: React.FC = () => {
  const [searchParams, setSearchParams] = useSearchParams();
  const rawNumber = searchParams.get('batch_number');

  const [ponds, setPonds] = useState<Pond[]>([]);
  const [loading, setLoading] = useState(false);
  const [errorKind, setErrorKind] = useState<ErrorKind | null>(null);
  const [result, setResult] = useState<BatchTraceability | null>(null);
  const [input, setInput] = useState(rawNumber ?? '');
  const [copied, setCopied] = useState(false);
  const requestSeq = useRef(0);

  useEffect(() => {
    pondApi.getAll().then((res) => setPonds(res.data)).catch(() => undefined);
  }, []);

  useEffect(() => {
    setInput(rawNumber ?? '');
    setResult(null);
    setErrorKind(null);
    setCopied(false);
    if (rawNumber === null || rawNumber === '') {
      if (rawNumber === '') setErrorKind('invalid');
      return;
    }

    const seq = ++requestSeq.current;
    setLoading(true);
    analysisApi
      .traceByBatchNumber(rawNumber)
      .then((res) => {
        if (seq !== requestSeq.current) return;
        setResult(res.data);
        setErrorKind(null);
        // 后端对非规范输入/旧别名已 301 到规范地址（axios 自动跟随），
        // 这里把浏览器地址同样收敛为唯一规范地址：分享、刷新、跳转一致。
        const canonical = res.data.batch.batch_number;
        if (canonical !== rawNumber) {
          const next = new URLSearchParams();
          searchParams.forEach((value, key) => {
            if (key !== 'batch_number') next.set(key, value);
          });
          next.set('batch_number', canonical);
          setSearchParams(next, { replace: true });
        }
      })
      .catch((err) => {
        if (seq !== requestSeq.current) return;
        setResult(null);
        setErrorKind(classifyError(err));
      })
      .finally(() => {
        if (seq === requestSeq.current) setLoading(false);
      });
    // 仅以地址中的批次号为触发源
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rawNumber]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    // 站内手工查询同样走唯一规范地址（服务端负责规范化并回跳）
    setSearchParams(new URLSearchParams({ batch_number: input }));
  };

  const handleCopy = async () => {
    if (!result) return;
    const shareUrl = buildBatchTraceShareUrl(result.batch.batch_number);
    try {
      await navigator.clipboard.writeText(shareUrl);
    } catch {
      // 非安全上下文（http）下剪贴板可能不可用，降级为选中输入
      window.prompt('复制追溯链接：', shareUrl);
    }
    setCopied(true);
    window.setTimeout(() => setCopied(false), 2000);
  };

  const pondName = (pondId?: number) => {
    const pond = ponds.find((p) => p.id === pondId);
    return pond ? pond.name : '未知塘口';
  };

  return (
    <div className="space-y-6">
      <div>
        <Link
          to="/analysis"
          className="inline-flex items-center text-sm text-ocean-600 hover:text-ocean-800 mb-2"
        >
          <ArrowLeft size={16} className="mr-1" />
          返回养殖分析
        </Link>
        <h1 className="text-2xl font-bold text-gray-900">批次追溯</h1>
        <p className="text-gray-600 mt-1">
          本页地址即规范追溯链接，可直接复制分享；刷新或从追溯标签打开结果一致。
        </p>
      </div>

      <div className="card bg-ocean-50 border-ocean-200">
        <form onSubmit={handleSubmit} className="flex flex-col md:flex-row gap-4">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            className="input-field flex-1"
            placeholder="输入批次号进行追溯查询..."
            aria-label="批次号"
          />
          <button
            type="submit"
            disabled={loading}
            className="btn-primary flex items-center justify-center space-x-2 min-w-[120px]"
          >
            {loading ? (
              <Loader2 size={18} className="animate-spin" />
            ) : (
              <Search size={18} />
            )}
            <span>查询</span>
          </button>
          {result && (
            <button
              type="button"
              onClick={handleCopy}
              className="btn-secondary flex items-center justify-center space-x-2 min-w-[140px]"
            >
              {copied ? <Check size={18} /> : <Copy size={18} />}
              <span>{copied ? '已复制' : '复制分享链接'}</span>
            </button>
          )}
        </form>
      </div>

      {loading && (
        <div className="card flex items-center justify-center h-64">
          <Loader2 size={32} className="text-ocean-600 animate-spin" />
        </div>
      )}

      {!loading && errorKind && (
        <div className="p-4 bg-red-50 text-red-700 rounded-lg border border-red-200">
          {ERROR_TEXT[errorKind]}
        </div>
      )}

      {!loading && !errorKind && !result && !rawNumber && (
        <div className="card flex flex-col items-center justify-center h-64 text-gray-500">
          <Search size={48} className="mb-3 text-gray-300" />
          <p>请输入批次号，或直接打开追溯标签上的链接</p>
        </div>
      )}

      {!loading && result && (
        <>
          <div className="text-sm text-gray-500">
            规范批次号：
            <span className="font-mono text-ocean-700">{result.batch.batch_number}</span>
            <span className="ml-2">
              （当前地址为唯一规范地址
              {rawNumber !== result.batch.batch_number ? '，已从标签上的旧写法自动收敛' : ''}）
            </span>
          </div>
          <BatchTraceResult data={result} pondName={pondName} />
        </>
      )}
    </div>
  );
};

export default TracePage;
