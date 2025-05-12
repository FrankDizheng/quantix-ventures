export default function AboutPage() {
    return (
      <div className="bg-white text-gray-800 px-4 py-20">
        <div className="max-w-5xl mx-auto space-y-12">
          <h1 className="text-5xl font-bold font-serif text-center mb-6">About Quantix Ventures</h1>
          <p className="text-lg leading-relaxed text-gray-700 text-center max-w-3xl mx-auto font-sans">
            Quantix Ventures was founded with the vision of making global financial trading more transparent,
            data-driven, and accessible. We combine institutional-level insight with boutique flexibility to help
            clients trade smarter and invest confidently.
          </p>
          <div className="grid md:grid-cols-2 gap-10">
            <div>
              <h2 className="text-2xl font-semibold font-serif mb-3">Our Mission</h2>
              <p className="text-base text-gray-600 font-sans">
                To empower investors with real-time analysis, technical insight, and trading strategies that make sense
                — no hype, just actionable intelligence.
              </p>
            </div>
            <div>
              <h2 className="text-2xl font-semibold font-serif mb-3">What Sets Us Apart</h2>
              <p className="text-base text-gray-600 font-sans">
                We believe in simplicity backed by substance. Our reports are clear, our tools are tested, and our
                strategies are based on deep data and disciplined methodology.
              </p>
            </div>
          </div>
        </div>
      </div>
    );
  }
  