import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { TrendingUp, Target, Users, Search, Loader2 } from 'lucide-react';
import { analysisApi, batchApi, pondApi } from '../services/api';
import type { CultureCycleAnalysis, Batch, Pond, BatchTraceability } from '../types';
import { canonicalizeBatchNumber, BatchNumberFormatError, tracePath } from '../utils/batchNumber';

const Analysis: React.FC = () => {
  const navigate = useNavigate();
  const [batches, setBatches] = useState<Batch[]>([]);
  const [ponds, setPonds] = useState<Pond[]>([]);
  const [loading, setLoading] = useState(true);
  const [analysisLoading, setAnalysisLoading] = useState(false);
  const [traceabilityLoading, setTraceabilityLoading] = useState(false);
  const [selectedBatchId, setSelectedBatchId] = useState<number | null>(null);
  const [analysisData, setAnalysisData] = useState<CultureCycleAnalysis | null>(null);
  const [traceabilityData, setTraceabilityData] = useState<BatchTraceability | null>(null);
  const [searchBatchNumber, setSearchBatchNumber] = useState('');
  const [searchError, setSearchError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const fetchData = async () => {
    try {
      const [batchesRes, pondsRes] = await Promise.all([
        batchApi.getAll(),
        pondApi.getAll()
      ]);
      setBatches(batchesRes.data);
      setPonds(pondsRes.data);
    } catch (error) {
      console.error('Error fetching data:', error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
  }, []);

  const handleBatchSelect = async (batchId: number) => {
    setSelectedBatchId(batchId);
    setAnalysisLoading(true);
    setError(null);
    
    try {
      const response = await analysisApi.analyzeCycle(batchId);
      setAnalysisData(response.data);
    } catch (err) {
      setError('获取分析数据失败');
      console.error('Error fetching analysis:', err);
    } finally {
      setAnalysisLoading(false);
    }
  };

  const handleTraceability = async (batchId: number) => {
    setTraceabilityLoading(true);
    setError(null);
    
    try {
      const response = await analysisApi.batchTraceability(batchId);
      setTraceabilityData(response.data);
    } catch (err) {
      setError('获取追溯数据失败');
      console.error('Error fetching traceability:', err);
    } finally {
      setTraceabilityLoading(false);
    }
  };

  const handleSearchBatch = (e: React.FormEvent) => {
    e.preventDefault();
    setSearchError(null);
    try {
      // 统一站内规范跳转：分享链接、刷新、站内导航地址完全一致
      navigate(tracePath(canonicalizeBatchNumber(searchBatchNumber)));
    } catch (err) {
      setSearchError(err instanceof BatchNumberFormatError ? err.message : '批次号格式非法');
    }
  };

  const getBatchNumber = (batchId: number) => {
    const batch = batches.find(b => b.id === batchId);
    return batch ? batch.batch_number : '未知批次';
  };

  const getPondName = (pondId: number) => {
    const pond = ponds.find(p => p.id === pondId);
    return pond ? pond.name : '未知塘口';
  };

  const getSpecies = (batchId: number) => {
    const batch = batches.find(b => b.id === batchId);
    return batch ? batch.species : '-';
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-gray-500">加载中...</div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">养殖周期分析</h1>
        <p className="text-gray-600 mt-1">分析养殖周期指标，查看批次追溯信息</p>
      </div>

      <div className="card bg-ocean-50 border-ocean-200">
        <h2 className="text-lg font-semibold text-ocean-900 mb-4 flex items-center space-x-2">
          <Search size={20} />
          <span>批次追溯查询</span>
        </h2>
        <form onSubmit={handleSearchBatch} className="flex flex-col md:flex-row gap-4">
          <input
            type="text"
            value={searchBatchNumber}
            onChange={(e) => setSearchBatchNumber(e.target.value)}
            className="input-field flex-1"
            placeholder="输入批次号进行追溯查询..."
          />
          <button
            type="submit"
            className="btn-primary flex items-center justify-center space-x-2 min-w-[120px]"
          >
            <Search size={18} />
            <span>查询追溯</span>
          </button>
        </form>
        <p className="mt-2 text-xs text-gray-500">
          查询将打开统一规范的追溯地址（/trace?n=批次号），可直接复制分享给客服或客户。
        </p>

        {searchError && (
          <div className="mt-4 p-3 bg-red-100 text-red-700 rounded-lg">
            {searchError}
          </div>
        )}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="card">
          <h2 className="text-lg font-semibold text-gray-900 mb-4">选择养殖批次</h2>
          <div className="space-y-2 max-h-[500px] overflow-y-auto">
            {batches.length === 0 ? (
              <p className="text-gray-500 text-center py-4">暂无养殖批次</p>
            ) : (
              batches.map((batch) => (
                <div
                  key={batch.id}
                  onClick={() => handleBatchSelect(batch.id)}
                  className={`p-3 rounded-lg border cursor-pointer transition-all ${
                    selectedBatchId === batch.id
                      ? 'border-ocean-500 bg-ocean-50 ring-2 ring-ocean-200'
                      : 'border-gray-200 hover:border-ocean-300 hover:bg-gray-50'
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <div>
                      <p className="font-medium text-gray-900">{batch.batch_number}</p>
                      <p className="text-sm text-gray-500">{batch.species}</p>
                      <p className="text-xs text-gray-400">{getPondName(batch.pond_id)}</p>
                    </div>
                    <span className={`badge ${
                      batch.status === 'active' ? 'badge-success' :
                      batch.status === 'completed' ? 'badge-info' : 'badge-warning'
                    }`}>
                      {batch.status === 'active' ? '养殖中' :
                       batch.status === 'completed' ? '已完成' : '待开始'}
                    </span>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>

        <div className="lg:col-span-2 space-y-6">
          {analysisLoading ? (
            <div className="card flex items-center justify-center h-64">
              <Loader2 size={32} className="text-ocean-600 animate-spin" />
            </div>
          ) : analysisData && selectedBatchId ? (
            <>
              <div className="card">
                <h2 className="text-lg font-semibold text-gray-900 mb-4">
                  养殖周期分析 - {getBatchNumber(selectedBatchId)}
                </h2>
                <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                  <div className="p-4 bg-green-50 rounded-lg">
                    <div className="flex items-center space-x-3">
                      <Target className="text-green-600" size={24} />
                      <div>
                        <p className="text-sm text-green-600">成活率</p>
                        <p className="text-2xl font-bold text-green-700">
                          {analysisData.survival_rate ? `${analysisData.survival_rate.toFixed(1)}%` : '-'}
                        </p>
                      </div>
                    </div>
                  </div>

                  <div className="p-4 bg-orange-50 rounded-lg">
                    <div className="flex items-center space-x-3">
                      <TrendingUp className="text-orange-600" size={24} />
                      <div>
                        <p className="text-sm text-orange-600">料肉比</p>
                        <p className="text-2xl font-bold text-orange-700">
                          {analysisData.feed_conversion_ratio ? analysisData.feed_conversion_ratio.toFixed(2) : '-'}
                        </p>
                      </div>
                    </div>
                  </div>

                  <div className="p-4 bg-blue-50 rounded-lg">
                    <div className="flex items-center space-x-3">
                      <Users className="text-blue-600" size={24} />
                      <div>
                        <p className="text-sm text-blue-600">亩产量</p>
                        <p className="text-2xl font-bold text-blue-700">
                          {analysisData.yield_per_mu ? `${analysisData.yield_per_mu.toFixed(1)} 公斤` : '-'}
                        </p>
                      </div>
                    </div>
                  </div>
                </div>
              </div>

              {analysisData.cost_summary && (
                <div className="card">
                  <h3 className="font-semibold text-gray-900 mb-3">成本汇总</h3>
                  <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
                    <div className="text-center p-3 bg-red-50 rounded-lg">
                      <p className="text-sm text-red-600">饲料成本</p>
                      <p className="font-bold text-red-700">¥{analysisData.cost_summary.feed_cost.toLocaleString()}</p>
                    </div>
                    <div className="text-center p-3 bg-purple-50 rounded-lg">
                      <p className="text-sm text-purple-600">药品成本</p>
                      <p className="font-bold text-purple-700">¥{analysisData.cost_summary.medicine_cost.toLocaleString()}</p>
                    </div>
                    <div className="text-center p-3 bg-blue-50 rounded-lg">
                      <p className="text-sm text-blue-600">人工成本</p>
                      <p className="font-bold text-blue-700">¥{analysisData.cost_summary.labor_cost.toLocaleString()}</p>
                    </div>
                    <div className="text-center p-3 bg-yellow-50 rounded-lg">
                      <p className="text-sm text-yellow-600">电费</p>
                      <p className="font-bold text-yellow-700">¥{analysisData.cost_summary.electricity_cost.toLocaleString()}</p>
                    </div>
                    <div className="text-center p-3 bg-gray-100 rounded-lg">
                      <p className="text-sm text-gray-600">其他成本</p>
                      <p className="font-bold text-gray-700">¥{analysisData.cost_summary.other_cost.toLocaleString()}</p>
                    </div>
                    <div className="text-center p-3 bg-red-100 rounded-lg">
                      <p className="text-sm text-red-600">总成本</p>
                      <p className="font-bold text-red-800">¥{analysisData.cost_summary.total_cost.toLocaleString()}</p>
                    </div>
                  </div>
                </div>
              )}

              {analysisData.feeding_summary && (
                <div className="card">
                  <h3 className="font-semibold text-gray-900 mb-3">投喂汇总</h3>
                  <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
                    <div className="text-center p-3 bg-green-50 rounded-lg">
                      <p className="text-sm text-green-600">总投喂量</p>
                      <p className="font-bold text-green-700">{analysisData.feeding_summary.total_feed_weight.toLocaleString()} 公斤</p>
                    </div>
                    <div className="text-center p-3 bg-blue-50 rounded-lg">
                      <p className="text-sm text-blue-600">投喂次数</p>
                      <p className="font-bold text-blue-700">{analysisData.feeding_summary.feeding_count} 次</p>
                    </div>
                    <div className="text-center p-3 bg-ocean-50 rounded-lg">
                      <p className="text-sm text-ocean-600">平均日投喂</p>
                      <p className="font-bold text-ocean-700">{analysisData.feeding_summary.avg_daily_feed.toFixed(2)} 公斤/天</p>
                    </div>
                  </div>
                </div>
              )}

              <div className="card">
                <div className="flex items-center justify-between mb-4">
                  <h3 className="font-semibold text-gray-900">完整批次追溯</h3>
                  <button
                    onClick={() => handleTraceability(selectedBatchId)}
                    disabled={traceabilityLoading}
                    className="btn-secondary text-sm py-2"
                  >
                    {traceabilityLoading ? '加载中...' : '查看追溯详情'}
                  </button>
                </div>

                {traceabilityData && (
                  <div className="mt-4 p-4 bg-gray-50 rounded-lg">
                    <h4 className="font-medium text-gray-700 mb-3">
                      {traceabilityData.batch.batch_number} - {traceabilityData.batch.species}
                    </h4>
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
                      <div>
                        <p className="text-gray-500">投苗记录</p>
                        <p className="font-semibold">{traceabilityData.stocking_records?.length || 0} 条</p>
                      </div>
                      <div>
                        <p className="text-gray-500">投喂记录</p>
                        <p className="font-semibold">{traceabilityData.feeding_records?.length || 0} 条</p>
                      </div>
                      <div>
                        <p className="text-gray-500">水质监测</p>
                        <p className="font-semibold">{traceabilityData.water_quality_records?.length || 0} 条</p>
                      </div>
                      <div>
                        <p className="text-gray-500">销售记录</p>
                        <p className="font-semibold">{traceabilityData.harvest_sales?.length || 0} 条</p>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            </>
          ) : (
            <div className="card flex flex-col items-center justify-center h-64 text-gray-500">
              <TrendingUp size={48} className="mb-3 text-gray-300" />
              <p>请选择一个养殖批次查看分析数据</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default Analysis;
