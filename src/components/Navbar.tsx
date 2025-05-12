'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';

const navItems = [
  { name: 'Home', path: '/' },
  { name: 'About', path: '/about' },
  { name: 'Products', path: '/products' },
  { name: 'Contact', path: '/contact' },
];

export default function Navbar() {
  const pathname = usePathname();

  return (
    <nav className="bg-gray-900 text-white">
      <div className="max-w-7xl mx-auto px-4 py-4 flex justify-between items-center">
        {/* Logo */}
        <div className="text-xl font-bold tracking-wide text-white">
          <Link href="/">Quantix Ventures</Link>
        </div>

        {/* Menu Items */}
        <div className="hidden md:flex items-center space-x-6 text-sm">
          {navItems.slice(0, 3).map((item) => (
            <Link
              key={item.path}
              href={item.path}
              className={`hover:text-blue-400 ${
                pathname === item.path ? 'text-blue-400 font-semibold' : ''
              }`}
            >
              {item.name}
            </Link>
          ))}
          {/* Contact Button */}
          <Link href="/contact">
            <button className="ml-4 bg-white text-gray-900 px-4 py-2 rounded shadow hover:bg-gray-100 text-sm">
              Contact Us
            </button>
          </Link>
        </div>
      </div>
    </nav>
  );
}
