import React, { useState } from 'react';

const HomePage = () => {
  const [pattern, setPattern] = useState('');
  const [explanation, setExplanation] = useState('');

  const handleExplain = async () => {
    const response = await fetch('/api/explain', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pattern }),
    });
    const data = await response.json();
    setExplanation(data.explanation);
  };

  return (
    <div>
      <h1>{{tool_name}}</h1>
      <input
        type="text"
        value={pattern}
        onChange={(e) => setPattern(e.target.value)}
        placeholder="Enter regex pattern"
      />
      <button onClick={handleExplain}>Explain</button>
      <p>{explanation}</p>
    </div>
  );
};

export default HomePage;
