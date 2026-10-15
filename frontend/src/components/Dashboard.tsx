import React, { useEffect, useState } from "react";
import { RevenueSummary } from "./RevenueSummary";
import { SecureAPI } from "../lib/secureApi";
import { useAuth } from "../contexts/AuthContext.new";

const Dashboard: React.FC = () => {
  const { user } = useAuth();
  return <TenantDashboard key={`${user?.id}:${user?.tenant_id}`} />;
};

const TenantDashboard: React.FC = () => {
  const [properties, setProperties] = useState<Array<{ id: string; name: string }>>([]);
  const [selectedProperty, setSelectedProperty] = useState('');
  const [period, setPeriod] = useState('2024-03');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    SecureAPI.getDashboardProperties().then((items) => {
      if (!cancelled) {
        setProperties(items);
        setSelectedProperty(items[0]?.id || '');
      }
    }).catch(() => {
      if (!cancelled) setError('Failed to load properties');
    }).finally(() => {
      if (!cancelled) setLoading(false);
    });
    return () => { cancelled = true; };
  }, []);

  return (
    <div className="p-4 lg:p-6 min-h-full">
      <div className="max-w-7xl mx-auto">
        <h1 className="text-2xl font-bold mb-6 text-gray-900">Property Management Dashboard</h1>

        <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-4 lg:p-6">
          <div className="mb-6">
            <div className="flex flex-col sm:flex-row sm:justify-between sm:items-start gap-4">
              <div>
                <h2 className="text-lg lg:text-xl font-medium text-gray-900 mb-2">Revenue Overview</h2>
                <p className="text-sm lg:text-base text-gray-600">
                  Monthly performance insights for your properties
                </p>
              </div>
              
              {/* Property Selector */}
              <div className="flex flex-col sm:items-end">
                <label htmlFor="dashboard-month" className="text-xs font-medium text-gray-700 mb-1">Reporting Month</label>
                <input
                  id="dashboard-month"
                  type="month"
                  min="0002-01"
                  max="9998-12"
                  required
                  value={period}
                  onChange={(e) => setPeriod(e.target.value)}
                  className="block w-full px-3 py-2 mb-3 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500 text-sm"
                />
                <label htmlFor="dashboard-property" className="text-xs font-medium text-gray-700 mb-1">Select Property</label>
                <select
                  id="dashboard-property"
                  disabled={loading || properties.length === 0}
                  value={selectedProperty}
                  onChange={(e) => setSelectedProperty(e.target.value)}
                  className="block w-full sm:w-auto min-w-[200px] px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500 text-sm"
                >
                  {properties.map((property) => (
                    <option key={property.id} value={property.id}>
                      {property.name}
                    </option>
                  ))}
                </select>
              </div>
            </div>
          </div>

          <div className="space-y-6">
            {loading && <p role="status">Loading properties...</p>}
            {error && <p role="alert" className="text-red-600">{error}</p>}
            {!loading && !error && !selectedProperty && <p>No properties available.</p>}
            {selectedProperty && period && (
              <RevenueSummary key={`${selectedProperty}:${period}`} propertyId={selectedProperty}
                year={Number(period.split('-')[0])} month={Number(period.split('-')[1])} />
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

export default Dashboard;
