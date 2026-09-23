import React from 'react';
import type { BatchTraceability } from '../types';

interface BatchTraceResultProps {
  data: BatchTraceability;
  pondName: (pondId?: number) => string;
}

/** 批次追溯结果的完整只读视图，分析页与 /trace 分享页共用同一渲染。 */
const BatchTraceResult: React.FC<BatchTraceResultProps> = ({ data, pondName }) => {
  return (
    <div className="p-4 bg-white rounded-lg border border-ocean-200">
      <h3 className="font-semibold text-gray-900 mb-4">
        批次追溯结果 - {data.batch.batch_number}
      </h3>
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        <div className="p-3 bg-blue-50 rounded-lg">
          <p className="text-sm text-blue-600">养殖品种</p>
          <p className="font-semibold">{data.batch.species}</p>
        </div>
        <div className="p-3 bg-green-50 rounded-lg">
          <p className="text-sm text-green-600">塘口</p>
          <p className="font-semibold">{pondName(data.batch.pond_id)}</p>
        </div>
        <div className="p-3 bg-purple-50 rounded-lg">
          <p className="text-sm text-purple-600">状态</p>
          <p className="font-semibold">{data.batch.status}</p>
        </div>
      </div>

      <div className="mt-6 space-y-4">
        <div>
          <h4 className="font-medium text-gray-700 mb-2">投苗记录 ({data.stocking_records?.length || 0})</h4>
          {data.stocking_records && data.stocking_records.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="table text-sm">
                <thead>
                  <tr>
                    <th>投苗日期</th>
                    <th>数量</th>
                    <th>来源</th>
                  </tr>
                </thead>
                <tbody>
                  {data.stocking_records.map((record, idx) => (
                    <tr key={idx}>
                      <td>{record.stocking_date}</td>
                      <td>{record.quantity} 尾</td>
                      <td>{record.source || '-'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="text-gray-500 text-sm">暂无投苗记录</p>
          )}
        </div>

        <div>
          <h4 className="font-medium text-gray-700 mb-2">投喂记录 ({data.feeding_records?.length || 0})</h4>
          {data.feeding_records && data.feeding_records.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="table text-sm">
                <thead>
                  <tr>
                    <th>投喂日期</th>
                    <th>饲料类型</th>
                    <th>数量</th>
                  </tr>
                </thead>
                <tbody>
                  {data.feeding_records.map((record, idx) => (
                    <tr key={idx}>
                      <td>{record.feeding_date}</td>
                      <td>{record.feed_type}</td>
                      <td>{record.quantity} {record.unit}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="text-gray-500 text-sm">暂无投喂记录</p>
          )}
        </div>

        <div>
          <h4 className="font-medium text-gray-700 mb-2">水质监测记录 ({data.water_quality_records?.length || 0})</h4>
          {data.water_quality_records && data.water_quality_records.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="table text-sm">
                <thead>
                  <tr>
                    <th>监测日期</th>
                    <th>水温</th>
                    <th>PH值</th>
                    <th>溶氧</th>
                  </tr>
                </thead>
                <tbody>
                  {data.water_quality_records.map((record, idx) => (
                    <tr key={idx}>
                      <td>{record.record_date}</td>
                      <td>{record.water_temperature}°C</td>
                      <td>{record.ph_value}</td>
                      <td>{record.dissolved_oxygen} mg/L</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="text-gray-500 text-sm">暂无水质监测记录</p>
          )}
        </div>

        <div>
          <h4 className="font-medium text-gray-700 mb-2">用药记录 ({data.medication_records?.length || 0})</h4>
          {data.medication_records && data.medication_records.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="table text-sm">
                <thead>
                  <tr>
                    <th>用药日期</th>
                    <th>药品名称</th>
                    <th>用量</th>
                  </tr>
                </thead>
                <tbody>
                  {data.medication_records.map((record, idx) => (
                    <tr key={idx}>
                      <td>{record.medication_date}</td>
                      <td>{record.medication_name}</td>
                      <td>{record.dosage} {record.unit}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="text-gray-500 text-sm">暂无用药记录</p>
          )}
        </div>

        <div>
          <h4 className="font-medium text-gray-700 mb-2">成本记录 ({data.cost_records?.length || 0})</h4>
          {data.cost_records && data.cost_records.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="table text-sm">
                <thead>
                  <tr>
                    <th>日期</th>
                    <th>费用类型</th>
                    <th>金额</th>
                  </tr>
                </thead>
                <tbody>
                  {data.cost_records.map((record, idx) => (
                    <tr key={idx}>
                      <td>{record.cost_date}</td>
                      <td>{record.cost_type}</td>
                      <td className="text-red-600">¥{record.amount}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="text-gray-500 text-sm">暂无成本记录</p>
          )}
        </div>

        <div>
          <h4 className="font-medium text-gray-700 mb-2">销售记录 ({data.harvest_sales?.length || 0})</h4>
          {data.harvest_sales && data.harvest_sales.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="table text-sm">
                <thead>
                  <tr>
                    <th>销售日期</th>
                    <th>重量</th>
                    <th>单价</th>
                    <th>总金额</th>
                    <th>买家</th>
                  </tr>
                </thead>
                <tbody>
                  {data.harvest_sales.map((record, idx) => (
                    <tr key={idx}>
                      <td>{record.sale_date}</td>
                      <td>{record.weight} 公斤</td>
                      <td>¥{record.unit_price}/公斤</td>
                      <td className="text-green-600 font-medium">¥{record.total_amount}</td>
                      <td>{record.buyer || '-'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="text-gray-500 text-sm">暂无销售记录</p>
          )}
        </div>
      </div>
    </div>
  );
};

export default BatchTraceResult;
