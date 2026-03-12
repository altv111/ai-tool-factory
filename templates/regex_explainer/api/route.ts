import { NextApiRequest, NextApiResponse } from 'next';

const explainRegex = (req: NextApiRequest, res: NextApiResponse) => {
  const { pattern } = req.body;
  // Simple explanation logic (placeholder)
  const explanation = `Explanation of ${pattern}`;
  res.status(200).json({ explanation });
};

export default explainRegex;
