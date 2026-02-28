import React from 'react';

interface GlassCardProps {
  children: React.ReactNode;
  className?: string;
  title?: string;
  glow?: boolean;
}

export const GlassCard: React.FC<GlassCardProps> = ({ children, className = "", title, glow = false }) => {
  return (
    <div 
      className={`
        glass-card rounded-2xl p-6 transition-all duration-300 hover:scale-[1.01]
        ${glow ? 'hover:shadow-[0_0_20px_rgba(255,255,255,0.2)]' : ''}
        ${className}
      `}
    >
      {title && (
        <h3 className="text-xl font-bold mb-4 text-transparent bg-clip-text bg-gradient-to-r from-blue-200 to-purple-200 border-b border-white/10 pb-2">
          {title}
        </h3>
      )}
      <div className="relative z-10">
        {children}
      </div>
    </div>
  );
};
