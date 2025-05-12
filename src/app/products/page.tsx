export default function ProductsPage() {
    return (
      <div className="bg-gray-50 text-gray-800 px-4 py-20">
        <div className="max-w-6xl mx-auto space-y-20">
          {/* Title */}
          <div className="text-center space-y-4">
            <h1 className="text-5xl font-bold font-serif tracking-tight">Our Offerings</h1>
            <p className="text-lg text-gray-600 max-w-2xl mx-auto leading-relaxed font-sans">
              We provide analysis tools, insights, and personalized strategies tailored to both institutional and individual traders.
            </p>
          </div>
  
          {/* Grid */}
          <div className="grid md:grid-cols-3 gap-10 text-left">
            <div className="bg-white p-6 rounded-xl shadow-md hover:shadow-lg transition-all">
              <h2 className="text-xl font-semibold text-blue-700 mb-2 font-serif">Daily Market Reports</h2>
              <p className="text-base text-gray-700 font-sans">
                Fresh, concise analysis on major indices, forex pairs, and commodities every market day.
              </p>
            </div>
            <div className="bg-white p-6 rounded-xl shadow-md hover:shadow-lg transition-all">
              <h2 className="text-xl font-semibold text-blue-700 mb-2 font-serif">Strategy Playbooks</h2>
              <p className="text-base text-gray-700 font-sans">
                Step-by-step trading strategies for momentum, breakout, and value investors — updated weekly.
              </p>
            </div>
            <div className="bg-white p-6 rounded-xl shadow-md hover:shadow-lg transition-all">
              <h2 className="text-xl font-semibold text-blue-700 mb-2 font-serif">Member Reports</h2>
              <p className="text-base text-gray-700 font-sans">
                For premium members: long-form insights with multi-timeframe setups and technical deep dives.
              </p>
            </div>
          </div>
        </div>
      </div>
    );
  }
  